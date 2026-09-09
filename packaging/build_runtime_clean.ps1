param([Parameter(Mandatory=$true)][ValidateSet('cu121','cu128')][string]$Runtime, [Parameter(Mandatory=$true)][string]$PythonDistribution, [Parameter(Mandatory=$true)][string]$Stage)
$ErrorActionPreference='Stop'
$stageRoot=[IO.Path]::GetFullPath($Stage)
$systemTemp=[IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Temp'))
if (-not $stageRoot.StartsWith($systemTemp + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Build stage must be inside system Temp' }
$sourceRoot=(Resolve-Path -LiteralPath $PythonDistribution).Path
$destination=Join-Path $stageRoot ('runtime-' + $Runtime)
if (Test-Path -LiteralPath $destination) { throw 'Use a new runtime staging directory' }
New-Item -ItemType Directory -Path $destination -Force | Out-Null
# PythonDistribution must be an extracted CPython 3.10.21 install_only standalone archive, not a venv.
if (Test-Path -LiteralPath (Join-Path $sourceRoot 'pyvenv.cfg')) { throw 'A virtualenv is not a standalone Python distribution' }
Get-ChildItem -LiteralPath $sourceRoot -Recurse -Force | Where-Object LinkType | ForEach-Object { throw 'The Python distribution must not contain links' }
Copy-Item -LiteralPath (Join-Path $sourceRoot 'python.exe') -Destination $destination
Get-ChildItem -LiteralPath $sourceRoot -Force | Where-Object Name -ne 'python.exe' | Copy-Item -Destination $destination -Recurse
$env:TEMP=$stageRoot; $env:TMP=$stageRoot; $env:PIP_CACHE_DIR=Join-Path $stageRoot 'pip-cache'; $env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONUTF8='1'
$python=Join-Path $destination 'python.exe'
$requirements=Join-Path (Split-Path -Parent $PSScriptRoot) ('requirements-runtime-' + $Runtime + '.txt')
& $python -B -m pip install --break-system-packages 'setuptools==80.10.2' 'wheel==0.48.0' 'Cython==3.3.0'
if ($LASTEXITCODE -ne 0) { throw 'Build dependency setup failed' }
& $python -B -m pip install --break-system-packages --no-build-isolation --extra-index-url ('https://download.pytorch.org/whl/' + $Runtime) -r $requirements
if ($LASTEXITCODE -ne 0) { throw 'Runtime dependency installation failed' }
& $python -B -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Runtime dependency check failed' }
& $python -B -c 'import ctypes; ctypes.windll.kernel32.SetErrorMode(3); import torch,torchaudio,wetext,kaldifst,soundfile; print(torch.__version__,torchaudio.__version__)'
if ($LASTEXITCODE -ne 0) { throw 'Runtime DLL/import check failed' }
& $python -B (Join-Path $PSScriptRoot 'collect_licenses.py') (Join-Path $destination 'Lib\site-packages') (Join-Path $destination 'THIRD_PARTY_LICENSES')
Copy-Item -LiteralPath (Join-Path $destination 'LICENSE.txt') -Destination (Join-Path $destination 'THIRD_PARTY_LICENSES\PYTHON-LICENSE.txt')
$scripts=[IO.Path]::GetFullPath((Join-Path $destination 'Scripts'))
if (-not $scripts.StartsWith($stageRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe scripts cleanup target' }
if (Test-Path -LiteralPath $scripts) { Remove-Item -LiteralPath $scripts -Recurse -Force }
& $python -B (Join-Path $PSScriptRoot 'build_components.py') --stage $stageRoot --package $Runtime
if ($LASTEXITCODE -ne 0) { throw 'Runtime packaging failed' }
