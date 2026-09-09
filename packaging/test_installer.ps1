param([Parameter(Mandatory=$true)][string]$Installer, [Parameter(Mandatory=$true)][string]$FrozenDirectory, [Parameter(Mandatory=$true)][string]$Stage)
$ErrorActionPreference='Stop'
$testStage=[IO.Path]::GetFullPath($Stage)
$systemTemp=[IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Temp'))
if (-not $testStage.StartsWith($systemTemp + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Installer test must run in a unique system Temp directory' }
if(Test-Path -LiteralPath $testStage) { throw 'Use a new installer test directory' }
foreach($surface in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))) {
    $normalized=[IO.Path]::GetFullPath($surface)
    if($normalized -eq 'E:\BaiduSyncdisk' -or $normalized.StartsWith('E:\BaiduSyncdisk\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Test shortcuts would enter BaiduSyncdisk' }
}
if(Test-Path -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\CosyVoice-Desktop') { throw 'Use a clean test account; an existing app installation must not be overwritten' }
if(Test-Path -LiteralPath (Join-Path ([Environment]::GetFolderPath('Desktop')) 'CosyVoice 配音工作台.lnk')) { throw 'Test must not overwrite an existing desktop shortcut' }
New-Item -ItemType Directory -Path $testStage -Force | Out-Null
$installFolder=Join-Path $testStage '中文 安装目录'
$testTemp=Join-Path $testStage 'installer-temp'
New-Item -ItemType Directory -Path $testTemp -Force | Out-Null
$env:TEMP=$testTemp; $env:TMP=$testTemp
$sourceFolder=(Resolve-Path -LiteralPath $FrozenDirectory).Path
$process=Start-Process -FilePath $Installer -ArgumentList @('/S',('/D=' + $installFolder)) -WindowStyle Hidden -Wait -PassThru
if($process.ExitCode -ne 0) { throw ('Installer exited ' + $process.ExitCode) }
$sourceFiles=@(Get-ChildItem -LiteralPath $sourceFolder -Recurse -File)
foreach($source in $sourceFiles) {
    $relative=$source.FullName.Substring($sourceFolder.Length + 1)
    $target=Join-Path $installFolder $relative
    if(-not(Test-Path -LiteralPath $target)) { throw ('Installed file missing: ' + $relative) }
    if((Get-FileHash -LiteralPath $source.FullName -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash) { throw ('Installed file differs: ' + $relative) }
}
$sentinel=Join-Path $installFolder 'unowned-user-file.txt'
[IO.File]::WriteAllText($sentinel,'User file must survive uninstall.',[Text.UTF8Encoding]::new($false))
$uninstaller=Join-Path $installFolder 'Uninstall.exe'
$uninstall=Start-Process -FilePath $uninstaller -ArgumentList '/S' -WindowStyle Hidden -Wait -PassThru
$deadline=(Get-Date).AddSeconds(45)
while((Test-Path -LiteralPath (Join-Path $installFolder 'CosyVoice-Desktop.exe')) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 250 }
$remaining=@(Get-ChildItem -LiteralPath $installFolder -Recurse -File | Where-Object FullName -ne $sentinel)
if($remaining.Count) { throw ('Uninstall left owned files: ' + (($remaining | Select-Object -First 3 -ExpandProperty Name) -join ', ')) }
if(-not(Test-Path -LiteralPath $sentinel)) { throw 'Uninstaller deleted an unowned file' }
$result=[ordered]@{status='passed';installation_directory='Chinese characters and spaces';installed_file_count=$sourceFiles.Count;all_installed_sha256_match=$true;unowned_file_preserved=$true;owned_files_removed=$true;install_exit_code=$process.ExitCode;uninstall_exit_code=$uninstall.ExitCode;installer_sha256=(Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLower()}
$result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $testStage 'installer-verification.json') -Encoding utf8
$result | ConvertTo-Json
