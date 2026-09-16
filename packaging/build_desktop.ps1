param([Parameter(Mandatory=$true)][string]$Python, [Parameter(Mandatory=$true)][string]$Stage, [Parameter(Mandatory=$true)][string]$MakeNSIS, [switch]$SkipFreeze)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$versionFile = Join-Path $projectRoot 'desktop_app\__init__.py'
$versionMatch = [regex]::Match((Get-Content -LiteralPath $versionFile -Raw -Encoding UTF8), '(?m)^__version__\s*=\s*(?<quote>["''])(?<version>[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)\k<quote>\s*$')
if (-not $versionMatch.Success) { throw 'Cannot read a valid __version__ from desktop_app/__init__.py' }
$appVersion = $versionMatch.Groups['version'].Value
$appFileVersion = ($appVersion -split '[-+]')[0] + '.0'
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
$installer=Join-Path $buildStage "CosyVoice-Desktop-$appVersion-windows-x64-setup.exe"
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
& $MakeNSIS '/INPUTCHARSET' 'UTF8' '/OUTPUTCHARSET' 'UTF8' ('/DBUILD_DIR=' + $binaryFolder) ('/DOUTPUT_FILE=' + $installer) ('/DUNINSTALL_LIST=' + $uninstallList) ('/DAPP_VERSION=' + $appVersion) ('/DAPP_FILE_VERSION=' + $appFileVersion) (Join-Path $PSScriptRoot 'installer.nsi')
if ($LASTEXITCODE -ne 0) { throw 'NSIS build failed' }
Compress-Archive -LiteralPath $binaryFolder -DestinationPath (Join-Path $buildStage "CosyVoice-Desktop-$appVersion-windows-x64-portable.zip") -Force
Get-FileHash -LiteralPath $installer -Algorithm SHA256
