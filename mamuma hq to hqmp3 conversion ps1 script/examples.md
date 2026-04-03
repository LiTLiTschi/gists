# Re-encode everything, keeping existing Rekordbox tags (BPM, key, rating, genre…)
.Convert-ToMp3.ps1 -InputFolder D:FLAC -OutputFolder D:MP3 -OverwriteExisting -PreserveMetadata

# Re-encode and overwrite, discarding old tags (takes metadata from source file)
.Convert-ToMp3.ps1 -InputFolder D:FLAC -OutputFolder D:MP3 -OverwriteExisting

# Default behavior unchanged — skip files that already exist in output
.Convert-ToMp3.ps1 -InputFolder D:FLAC -OutputFolder D:MP3