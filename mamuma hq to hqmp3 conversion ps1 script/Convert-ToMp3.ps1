param(
    [string]$InputFolder,
    [string]$OutputFolder,
    [switch]$DeleteAfter,
    [switch]$OverwriteExisting,
    [switch]$PreserveMetadata,
    [int]$Jobs = 4,
    [string]$Ffmpeg = 'ffmpeg'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'Continue'
$validExt = @('.m4a','.wav','.flac','.ogg','.aac','.wma','.opus','.aiff','.mp3')

function Resolve-FullPath([string]$PathText) {
    return [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $PathText).Path)
}

function Ensure-Dir([string]$p) {
    if (-not (Test-Path -LiteralPath $p)) {
        New-Item -ItemType Directory -Path $p -Force | Out-Null
    }
}

if (-not $InputFolder)  { $InputFolder  = Read-Host 'Enter INPUT folder path' }
if (-not $OutputFolder) { $OutputFolder = Read-Host 'Enter OUTPUT folder path' }

if (-not (Test-Path -LiteralPath $InputFolder)) {
    throw "Input folder not found: $InputFolder"
}

if ($PreserveMetadata -and -not $OverwriteExisting) {
    Write-Warning '-PreserveMetadata has no effect without -OverwriteExisting; both flags must be used together.'
}

Ensure-Dir $OutputFolder

$inputRoot  = Resolve-FullPath $InputFolder
$outputRoot = Resolve-FullPath $OutputFolder

Write-Host "Scanning : $inputRoot"
Write-Host "Output   : $outputRoot"
Write-Host "Jobs     : $Jobs"
Write-Host "Delete   : $DeleteAfter"
Write-Host "Overwrite: $OverwriteExisting"
Write-Host "Preserve : $PreserveMetadata"
Write-Host ""

$all = Get-ChildItem -LiteralPath $inputRoot -Recurse -File | Where-Object {
    $validExt -contains $_.Extension.ToLowerInvariant()
}

$tasks   = [System.Collections.Generic.List[object]]::new()
$skipped = 0

foreach ($f in $all) {
    $relative = $f.FullName.Substring($inputRoot.Length).TrimStart([System.IO.Path]::DirectorySeparatorChar)
    $dest     = Join-Path $outputRoot $relative
    $dest     = [System.IO.Path]::ChangeExtension($dest, '.mp3')

    if (Test-Path -LiteralPath $dest) {
        if ($OverwriteExisting) {
            $tasks.Add([pscustomobject]@{ Src = $f.FullName; Dest = $dest })
        } else {
            $skipped++
        }
    } else {
        $tasks.Add([pscustomobject]@{ Src = $f.FullName; Dest = $dest })
    }
}

$total = $tasks.Count

Write-Host "Found    : $($all.Count) supported files"
Write-Host "Queued   : $total files"
if ($skipped -gt 0) {
    Write-Host "Skipped  : $skipped files (already exist - pass -OverwriteExisting to re-convert)"
}
Write-Host ""

if ($total -eq 0) {
    if ($all.Count -eq 0) {
        Write-Host 'No supported audio files found in input folder.'
    } else {
        Write-Host 'Nothing to convert - all output files already exist.'
        Write-Host 'Pass -OverwriteExisting to force re-conversion.'
    }
    exit 0
}

# ---------------------------------------------------------------------------
# Worker script block
#
# Preserve-metadata workflow (requires -OverwriteExisting AND -PreserveMetadata
# AND an already-existing destination file):
#
#   1. Rename existing dest  ->  dest.~metabak   (keep old tags safe)
#   2. Convert source        ->  dest.~tmpaudio  (fresh 320k encode, no tags)
#   3. Re-mux via ffmpeg:
#        audio  from dest.~tmpaudio
#        + ALL tags from dest.~metabak   (title, genre, BPM, key, rating, ...)
#        -> final dest                   (-c:a copy, no re-encode)
#   4. Delete dest.~metabak and dest.~tmpaudio
#
# On any failure the backup is restored so the original file is never lost.
# ---------------------------------------------------------------------------
$scriptBlock = {
    param($Task, $Ffmpeg, $DeleteAfter, $OverwriteExisting, $PreserveMetadata)

    $destDir = Split-Path -Parent $Task.Dest
    if (-not (Test-Path -LiteralPath $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }

    $ok = $false

    # Re-check at job time (another job may have created/deleted the file)
    $doPreserve = $OverwriteExisting -and $PreserveMetadata -and (Test-Path -LiteralPath $Task.Dest)

    if ($doPreserve) {

        $bakPath = $Task.Dest + '.~metabak'
        $tmpPath = $Task.Dest + '.~tmpaudio.mp3'

        try {
            # Step 1 - protect the existing file (and its tags) as a backup
            Rename-Item -LiteralPath $Task.Dest `
                        -NewName ([System.IO.Path]::GetFileName($bakPath)) `
                        -ErrorAction Stop

            # Step 2 - encode from source (no metadata; tags come from backup)
            $cArgs = @(
                '-threads', '0',
                '-i',        $Task.Src,
                '-vn',
                '-map_metadata', '-1',
                '-c:a',      'libmp3lame',
                '-b:a',      '320k',
                '-compression_level', '0',
                '-y',        $tmpPath
            )
            & $Ffmpeg @cArgs 1>$null 2>$null
            $convertOk = ($LASTEXITCODE -eq 0)

            if ($convertOk) {
                # Step 3 - re-mux: 320k audio + ALL tags from backup -> final dest
                #   -map_metadata 1  ->  pull every ID3 tag from input[1] (the backup):
                #                        title, artist, album, genre, comment, BPM,
                #                        initial key, rating (POPM), track #, year, label ...
                #   -c:a copy        ->  stream-copy the already-encoded audio; no re-encode
                $mArgs = @(
                    '-i',            $tmpPath,
                    '-i',            $bakPath,
                    '-map',          '0:a',
                    '-map_metadata', '1',
                    '-c:a',          'copy',
                    '-y',            $Task.Dest
                )
                & $Ffmpeg @mArgs 1>$null 2>$null
                $mergeOk = ($LASTEXITCODE -eq 0)

                if ($mergeOk) {
                    $ok = $true
                    Remove-Item -LiteralPath $bakPath -Force -ErrorAction SilentlyContinue
                } else {
                    # Merge failed - remove any partial dest, restore the backup
                    if (Test-Path -LiteralPath $Task.Dest) {
                        Remove-Item -LiteralPath $Task.Dest -Force -ErrorAction SilentlyContinue
                    }
                    Rename-Item -LiteralPath $bakPath `
                                -NewName ([System.IO.Path]::GetFileName($Task.Dest)) `
                                -ErrorAction SilentlyContinue
                }
            } else {
                # Convert failed - restore backup immediately
                Rename-Item -LiteralPath $bakPath `
                            -NewName ([System.IO.Path]::GetFileName($Task.Dest)) `
                            -ErrorAction SilentlyContinue
            }
        }
        catch {
            # Unexpected error - best-effort restore so original file is not lost
            if ((Test-Path -LiteralPath $bakPath) -and -not (Test-Path -LiteralPath $Task.Dest)) {
                Rename-Item -LiteralPath $bakPath `
                            -NewName ([System.IO.Path]::GetFileName($Task.Dest)) `
                            -ErrorAction SilentlyContinue
            }
        }
        finally {
            # Always remove temp audio, even on failure
            if (Test-Path -LiteralPath $tmpPath) {
                Remove-Item -LiteralPath $tmpPath -Force -ErrorAction SilentlyContinue
            }
        }

    } else {
        # Standard path: fresh file, or overwrite without -PreserveMetadata
        $cArgs = @(
            '-threads', '0',
            '-i',        $Task.Src,
            '-vn',
            '-map_metadata', '0',
            '-c:a',      'libmp3lame',
            '-b:a',      '320k',
            '-compression_level', '0',
            '-y',        $Task.Dest
        )
        & $Ffmpeg @cArgs 1>$null 2>$null
        $ok = ($LASTEXITCODE -eq 0)

        if (-not $ok -and (Test-Path -LiteralPath $Task.Dest)) {
            Remove-Item -LiteralPath $Task.Dest -Force -ErrorAction SilentlyContinue
        }
    }

    if ($ok -and $DeleteAfter) {
        Remove-Item -LiteralPath $Task.Src -Force -ErrorAction SilentlyContinue
    }

    [pscustomobject]@{
        Src  = $Task.Src
        Dest = $Task.Dest
        Ok   = $ok
    }
}

$queue = [System.Collections.Generic.Queue[object]]::new()
foreach ($t in $tasks) { $queue.Enqueue($t) }

$running   = @()
$doneCount = 0
$okCount   = 0
$failCount = 0

while ($queue.Count -gt 0 -or $running.Count -gt 0) {

    while ($queue.Count -gt 0 -and $running.Count -lt $Jobs) {
        $task = $queue.Dequeue()
        Write-Host ("[START] {0}" -f $task.Src)
        $running += Start-Job -ScriptBlock $scriptBlock `
                              -ArgumentList $task, $Ffmpeg, $DeleteAfter, $OverwriteExisting, $PreserveMetadata
    }

    $newRunning = @()

    foreach ($job in $running) {
        if ($job.State -eq 'Running') {
            $newRunning += $job
            continue
        }

        $res = Receive-Job $job -ErrorAction SilentlyContinue
        Remove-Job $job -Force -ErrorAction SilentlyContinue

        if ($res) {
            $doneCount++

            if ($res.Ok) {
                $okCount++
                Write-Host ("[DONE ] {0}" -f $res.Src)
            } else {
                $failCount++
                Write-Host ("[FAIL ] {0}" -f $res.Src)
            }
        }
    }

    $running = $newRunning

    $pct = [math]::Round((($doneCount + 0.0) / $total) * 100, 1)
    Write-Progress `
        -Activity 'Converting to MP3' `
        -Status   "$doneCount / $total done | OK $okCount | Fail $failCount" `
        -PercentComplete $pct

    Start-Sleep -Milliseconds 200
}

Write-Progress -Activity 'Converting to MP3' -Completed
Write-Host ""
Write-Host '============================================================'
Write-Host "Done: $doneCount total | OK $okCount | Failed $failCount"
Write-Host '============================================================'
