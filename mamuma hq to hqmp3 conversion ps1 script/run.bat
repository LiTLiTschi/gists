@echo off
powershell.exe -ExecutionPolicy Bypass -File "%~dp0Convert-ToMp3.ps1" -InputFolder "D:\music\hq" -OutputFolder "D:\music\library" -OverwriteExisting -PreserveMetadata -DeleteAfter
