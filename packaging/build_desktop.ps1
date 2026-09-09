param([Parameter(Mandatory=$true)][string]$Python, [Parameter(Mandatory=$true)][string]$Stage, [Parameter(Mandatory=$true)][string]$MakeNSIS, [switch]$SkipFreeze)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$buildStage = [IO.Path]::GetFullPath($Stage)
$systemTemp = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Temp'))
if (-not $buildStage.StartsWith($systemTemp + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Build stage must be inside system Temp' }
New-Item -ItemType Directory -Path $buildStage -Force | Out-Null
$env:TMP=$buildStage; $env:TEMP=$buildStage; $env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONUTF8='1'; $env:PYINSTALLER_CONFIG_DIR=Join-Path $buildStage 'pyinstaller-cache'
$env:PATH=(Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
if (-not $SkipFreeze) {
    & $Python -B -m PyInstaller --noconfirm --clean --distpath (Join-Path $buildStage 'dist') --workpath (Join-Path $buildStage 'work') (Join-Path $PSScriptRoot 'desktop.spec')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
}
$binaryFolder=Join-Path $buildStage 'dist\CosyVoice-Desktop'
if (-not (Test-Path -LiteralPath (Join-Path $binaryFolder 'CosyVoice-Desktop.exe'))) { throw 'Frozen application is missing' }
$installer=Join-Path $buildStage 'CosyVoice-Desktop-0.1.0-alpha.1-windows-x64-setup.exe'
$uninstallList=Join-Path $buildStage 'uninstall-files.nsh'
$uninstallLines = [Collections.Generic.List[string]]::new()
Get-ChildItem -LiteralPath $binaryFolder -Recurse -File | ForEach-Object {
    $relative = $_.FullName.Substring($binaryFolder.Length + 1).Replace('$','$$').Replace('"','$\"')
    $uninstallLines.Add('Delete "$INSTDIR\' + $relative + '"')
}
Get-ChildItem -LiteralPath $binaryFolder -Recurse -Directory | Sort-Object { $_.FullName.Length } -Descending | ForEach-Object {
    $relative = $_.FullName.Substring($binaryFolder.Length + 1).Replace('$','$$').Replace('"','$\"')
    $uninstallLines.Add('RMDir "$INSTDIR\' + $relative + '"')
}
[IO.File]::WriteAllLines($uninstallList, $uninstallLines, [Text.UTF8Encoding]::new($false))
& $MakeNSIS '/INPUTCHARSET' 'UTF8' '/OUTPUTCHARSET' 'UTF8' ('/DBUILD_DIR=' + $binaryFolder) ('/DOUTPUT_FILE=' + $installer) ('/DUNINSTALL_LIST=' + $uninstallList) (Join-Path $PSScriptRoot 'installer.nsi')
if ($LASTEXITCODE -ne 0) { throw 'NSIS build failed' }
Compress-Archive -LiteralPath $binaryFolder -DestinationPath (Join-Path $buildStage 'CosyVoice-Desktop-0.1.0-alpha.1-windows-x64-portable.zip') -Force
Get-FileHash -LiteralPath $installer -Algorithm SHA256
