[CmdletBinding()]
param(
    [Alias('src')][Parameter(Mandatory)][string]$SourceDir,
    [Alias('dst')][Parameter(Mandatory)][string]$DestDir,
    [ValidateRange(1,256)][int]$BufferSizeMB  = 8,
    [ValidateRange(1,64)] [int]$ThrottleLimit = 8,
    [ValidateRange(1,1000)][int]$ProgressEvery = 100
)

$ErrorActionPreference = 'Stop'

$helperFunctions = @'

function Read-Exact {
    param([System.IO.FileStream]$Stream, [byte[]]$Buffer, [int]$Count)
    $pos = 0
    while ($pos -lt $Count) {
        $n = $Stream.Read($Buffer, $pos, $Count - $pos)
        if ($n -le 0) { throw "Unexpected end of stream at position $pos / $Count" }
        $pos += $n
    }
}

function Get-Mp3TagLayout {
    param([Parameter(Mandatory)][string]$Path)
    $fs = [System.IO.FileStream]::new(
        $Path,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read,
        65536,
        [System.IO.FileOptions]::RandomAccess)
    try {
        $len = $fs.Length

        $id3v2 = [int64]0
        if ($len -ge 10) {
            $hdr = [byte[]]::new(10)
            $fs.Position = 0
            if ($fs.Read($hdr, 0, 10) -eq 10 -and
                $hdr[0] -eq 0x49 -and $hdr[1] -eq 0x44 -and $hdr[2] -eq 0x33) {
                $size = (($hdr[6] -band 0x7F) -shl 21) -bor
                        (($hdr[7] -band 0x7F) -shl 14) -bor
                        (($hdr[8] -band 0x7F) -shl 7)  -bor
                         ($hdr[9] -band 0x7F)
                $extra = if (($hdr[5] -band 0x10) -ne 0) { 10 } else { 0 }
                $id3v2 = [int64](10 + $size + $extra)
            }
        }

        $id3v1 = [int64]0
        if ($len -ge 128) {
            $buf = [byte[]]::new(128)
            $fs.Position = $len - 128
            if ($fs.Read($buf, 0, 128) -eq 128 -and
                $buf[0] -eq 0x54 -and $buf[1] -eq 0x41 -and $buf[2] -eq 0x47) {
                $id3v1 = [int64]128
            }
        }

        $apev2 = [int64]0
        if ($len -ge ($id3v1 + 32)) {
            $footerStart = $len - $id3v1 - 32
            $footer = [byte[]]::new(32)
            $fs.Position = $footerStart
            if ($fs.Read($footer, 0, 32) -eq 32 -and
                $footer[0] -eq 0x41 -and $footer[1] -eq 0x50 -and
                $footer[2] -eq 0x45 -and $footer[3] -eq 0x54 -and
                $footer[4] -eq 0x41 -and $footer[5] -eq 0x47 -and
                $footer[6] -eq 0x45 -and $footer[7] -eq 0x58) {
                $apeSize = [int64][BitConverter]::ToUInt32($footer, 12)
                if ($apeSize -ge 32 -and $apeSize -le ($len - $id3v1)) {
                    $apev2 = $apeSize
                    $headerStart = $footerStart - $apeSize
                    if ($headerStart -ge 0) {
                        $apehdr = [byte[]]::new(8)
                        $fs.Position = $headerStart
                        if ($fs.Read($apehdr, 0, 8) -eq 8 -and
                            $apehdr[0] -eq 0x41 -and $apehdr[1] -eq 0x50 -and
                            $apehdr[2] -eq 0x45 -and $apehdr[3] -eq 0x54 -and
                            $apehdr[4] -eq 0x41 -and $apehdr[5] -eq 0x47 -and
                            $apehdr[6] -eq 0x45 -and $apehdr[7] -eq 0x58) {
                            $apev2 += 32
                        }
                    }
                }
            }
        }

        return [pscustomobject]@{ Head = $id3v2; Tail = $id3v1 + $apev2 }
    }
    finally { $fs.Dispose() }
}

function Test-Mp3TagEqual {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Dest,
        [Parameter(Mandatory)][pscustomobject]$SrcLayout,
        [Parameter(Mandatory)][pscustomobject]$DstLayout
    )
    if ($SrcLayout.Head -ne $DstLayout.Head -or $SrcLayout.Tail -ne $DstLayout.Tail) { return $false }
    if ($SrcLayout.Head -eq 0 -and $SrcLayout.Tail -eq 0) { return $true }

    $srcFs = $null
    $dstFs = $null
    try {
        $srcFs = [System.IO.FileStream]::new($Source, [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read,
            65536, [System.IO.FileOptions]::RandomAccess)
        $dstFs = [System.IO.FileStream]::new($Dest, [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read,
            65536, [System.IO.FileOptions]::RandomAccess)

        if ($SrcLayout.Head -gt 0) {
            $n    = [int]$SrcLayout.Head
            $srcB = [byte[]]::new($n)
            $dstB = [byte[]]::new($n)
            $srcFs.Position = 0
            $dstFs.Position = 0
            Read-Exact $srcFs $srcB $n
            Read-Exact $dstFs $dstB $n
            if (-not [System.Linq.Enumerable]::SequenceEqual(
                    [System.Collections.Generic.IEnumerable[byte]]$srcB,
                    [System.Collections.Generic.IEnumerable[byte]]$dstB)) { return $false }
        }

        if ($SrcLayout.Tail -gt 0) {
            $n    = [int]$SrcLayout.Tail
            $srcB = [byte[]]::new($n)
            $dstB = [byte[]]::new($n)
            $srcFs.Position = $srcFs.Length - $SrcLayout.Tail
            $dstFs.Position = $dstFs.Length - $DstLayout.Tail
            Read-Exact $srcFs $srcB $n
            Read-Exact $dstFs $dstB $n
            if (-not [System.Linq.Enumerable]::SequenceEqual(
                    [System.Collections.Generic.IEnumerable[byte]]$srcB,
                    [System.Collections.Generic.IEnumerable[byte]]$dstB)) { return $false }
        }

        return $true
    }
    finally {
        if ($srcFs) { $srcFs.Dispose() }
        if ($dstFs) { $dstFs.Dispose() }
    }
}

function Copy-Range {
    param(
        [Parameter(Mandatory)][System.IO.FileStream]$InputStream,
        [Parameter(Mandatory)][System.IO.FileStream]$OutputStream,
        [Parameter(Mandatory)][int64]$Offset,
        [Parameter(Mandatory)][int64]$Count,
        [Parameter(Mandatory)][byte[]]$Buffer
    )
    if ($Count -le 0) { return }
    $InputStream.Position = $Offset
    $remaining = $Count
    while ($remaining -gt 0) {
        $toRead = if ($remaining -gt $Buffer.Length) { $Buffer.Length } else { [int]$remaining }
        $read = $InputStream.Read($Buffer, 0, $toRead)
        if ($read -le 0) { throw "Unexpected end of file while copying range." }
        $OutputStream.Write($Buffer, 0, $read)
        $remaining -= $read
    }
}

function Copy-FileFast {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Dest,
        [Parameter(Mandatory)][int]$BufferSize
    )
    $srcFs = [System.IO.FileStream]::new($Source, [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read,
        $BufferSize, [System.IO.FileOptions]::SequentialScan)
    try {
        $dstFs = [System.IO.FileStream]::new($Dest, [System.IO.FileMode]::Create,
            [System.IO.FileAccess]::Write, [System.IO.FileShare]::None,
            $BufferSize, [System.IO.FileOptions]::SequentialScan)
        try { $srcFs.CopyTo($dstFs, $BufferSize) }
        finally { $dstFs.Dispose() }
    }
    finally { $srcFs.Dispose() }
}

function Copy-Mp3MetadataRaw {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Dest,
        [Parameter(Mandatory)][int]$BufferSize,
        [Parameter(Mandatory)][pscustomobject]$SrcLayout,
        [Parameter(Mandatory)][pscustomobject]$DstLayout
    )
    $tmp    = "$Dest.tmp.$([guid]::NewGuid().ToString('N'))"
    $buffer = [byte[]]::new($BufferSize)
    $srcFs = $null; $dstFs = $null; $tmpFs = $null
    try {
        $srcFs = [System.IO.FileStream]::new($Source, [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read,
            $BufferSize, [System.IO.FileOptions]::SequentialScan)
        $dstFs = [System.IO.FileStream]::new($Dest, [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read,
            $BufferSize, [System.IO.FileOptions]::SequentialScan)
        $tmpFs = [System.IO.FileStream]::new($tmp, [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write, [System.IO.FileShare]::None,
            $BufferSize, [System.IO.FileOptions]::SequentialScan)

        $audioStart  = [int64]$DstLayout.Head
        $audioLength = [int64]($dstFs.Length - $DstLayout.Head - $DstLayout.Tail)
        if ($audioLength -lt 0) { throw "Destination MP3 appears malformed: $Dest" }

        if ($SrcLayout.Head -gt 0) {
            Copy-Range -InputStream $srcFs -OutputStream $tmpFs -Offset 0 -Count $SrcLayout.Head -Buffer $buffer
        }
        if ($audioLength -gt 0) {
            Copy-Range -InputStream $dstFs -OutputStream $tmpFs -Offset $audioStart -Count $audioLength -Buffer $buffer
        }
        if ($SrcLayout.Tail -gt 0) {
            Copy-Range -InputStream $srcFs -OutputStream $tmpFs -Offset ($srcFs.Length - $SrcLayout.Tail) -Count $SrcLayout.Tail -Buffer $buffer
        }
    }
    finally {
        if ($tmpFs) { $tmpFs.Dispose() }
        if ($dstFs) { $dstFs.Dispose() }
        if ($srcFs) { $srcFs.Dispose() }
    }
    try {
        [System.IO.File]::Replace($tmp, $Dest, $null, $true)
    }
    catch {
        if ([System.IO.File]::Exists($Dest)) { [System.IO.File]::Delete($Dest) }
        [System.IO.File]::Move($tmp, $Dest)
    }
    finally {
        if ([System.IO.File]::Exists($tmp)) { [System.IO.File]::Delete($tmp) }
    }
}
'@

# ── Main ──────────────────────────────────────────────────────────────────────

$SourceDir = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $SourceDir).Path)
[void][System.IO.Directory]::CreateDirectory($DestDir)
$DestDir = [System.IO.Path]::GetFullPath($DestDir)

if ($SourceDir.TrimEnd('\') -ieq $DestDir.TrimEnd('\')) { throw "SourceDir and DestDir must be different." }

$bufferSize = $BufferSizeMB * 1MB

Write-Host "Scanning source directory..."
$allFiles = [System.IO.Directory]::GetFiles($SourceDir, '*.mp3', [System.IO.SearchOption]::AllDirectories)
$total    = $allFiles.Count
Write-Host "Found $total MP3 files. Starting (ThrottleLimit=$ThrottleLimit)..."

# Thread-safe counters — workers increment directly, no collector pipeline needed
$cDone    = [System.Threading.Volatile]::Read([ref]0)
$counters  = [System.Collections.Concurrent.ConcurrentDictionary[string,int]]::new()
foreach ($k in 'copied','meta','skipped','errors') { [void]$counters.TryAdd($k, 0) }
$warnings  = [System.Collections.Concurrent.ConcurrentQueue[string]]::new()

$allFiles | ForEach-Object -Parallel {
    . ([scriptblock]::Create($using:helperFunctions))

    $src        = $_
    $srcDir     = $using:SourceDir
    $dstDir     = $using:DestDir
    $bufferSize = $using:bufferSize
    $counters   = $using:counters
    $warnings   = $using:warnings

    $rel       = [System.IO.Path]::GetRelativePath($srcDir, $src)
    $dst       = [System.IO.Path]::Combine($dstDir, $rel)
    $dstParent = [System.IO.Path]::GetDirectoryName($dst)
    [void][System.IO.Directory]::CreateDirectory($dstParent)

    if ($src -ieq $dst) {
        [void]$counters.AddOrUpdate('skipped', 1, [Func[string,int,int]]{ $args[1] + 1 })
        [void][System.Threading.Interlocked]::Increment([ref]$using:cDone)
        return
    }

    try {
        if ([System.IO.File]::Exists($dst)) {
            $srcLayout = Get-Mp3TagLayout -Path $src
            $dstLayout = Get-Mp3TagLayout -Path $dst

            if (Test-Mp3TagEqual -Source $src -Dest $dst -SrcLayout $srcLayout -DstLayout $dstLayout) {
                [void]$counters.AddOrUpdate('skipped', 1, [Func[string,int,int]]{ $args[1] + 1 })
            } else {
                Copy-Mp3MetadataRaw -Source $src -Dest $dst -BufferSize $bufferSize `
                                    -SrcLayout $srcLayout -DstLayout $dstLayout
                [void]$counters.AddOrUpdate('meta', 1, [Func[string,int,int]]{ $args[1] + 1 })
            }
        }
        else {
            Copy-FileFast -Source $src -Dest $dst -BufferSize $bufferSize
            [void]$counters.AddOrUpdate('copied', 1, [Func[string,int,int]]{ $args[1] + 1 })
        }
    }
    catch {
        [void]$counters.AddOrUpdate('errors', 1, [Func[string,int,int]]{ $args[1] + 1 })
        $warnings.Enqueue("Failed: $rel :: $($_.Exception.Message)")
    }

    [void][System.Threading.Interlocked]::Increment([ref]$using:cDone)

    # Throttled progress update — only the worker that hits a multiple of ProgressEvery prints
    $snap = [System.Threading.Volatile]::Read([ref]$using:cDone)
    if ($snap % $using:ProgressEvery -eq 0) {
        $pct = [int](($snap / $using:total) * 100)
        Write-Progress -Activity 'Syncing MP3s' `
            -Status ("$snap / $($using:total)  |  Copied: {0}  Meta: {1}  Skipped: {2}  Errors: {3}" -f
                $using:counters['copied'], $using:counters['meta'],
                $using:counters['skipped'], $using:counters['errors']) `
            -PercentComplete $pct
    }

} -ThrottleLimit $ThrottleLimit

# Drain any queued warnings
while (-not $warnings.IsEmpty) {
    $msg = $null
    if ($warnings.TryDequeue([ref]$msg)) { Write-Warning $msg }
}

Write-Progress -Activity 'Syncing MP3s' -Completed
Write-Host ("Done. Total={0}; Copied={1}; MetadataUpdated={2}; Skipped={3}; Errors={4}" -f
    $total, $counters['copied'], $counters['meta'], $counters['skipped'], $counters['errors'])
