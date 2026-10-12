# Builds the Windows package of PS5 Game Packer: the folder dist\ps5-game-packer (PyInstaller, onedir) and the zip
# dist\ps5-game-packer-<version>-windows-x64.zip. Run it ON WINDOWS with Python 3.12 installed:
#
#   powershell -ExecutionPolicy Bypass -File packaging\windows.ps1
#
# Parameters:
#   -Mkpfs <pip requirement or path>  engine to bundle (default: the MKPFS_PIN of ps5_game_packer\environment.py)
#   -Python <python.exe>              Python used to create the build venv (default "python")
#   -NoUnrar                          do not download UnRAR.exe from rarlab.com (the package will not read .rar)
#   -TestsOnly                        prepare the venv, run pytest and stop without building
param(
    [string]$Mkpfs = "",
    [string]$Python = "python",
    [switch]$NoUnrar,
    [switch]$TestsOnly
)
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Venv = Join-Path $Root "packaging\venv-win"
$Py = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "Creating the build venv at $Venv"
    & $Python -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "could not create the venv with $Python" }
}
& $Py -m pip install --quiet --upgrade pip
& $Py -m pip install --quiet --upgrade "customtkinter>=5.2" "pyzipper>=0.3" "psutil>=5.9" "pyinstaller>=6.6" "pillow>=9" "pytest>=7"
if ($LASTEXITCODE -ne 0) { throw "could not install the dependencies" }

if (-not $Mkpfs) {
    $Mkpfs = (& $Py -c "import sys; sys.path.insert(0, r'$Root'); from ps5_game_packer import environment; print(environment.MKPFS_PIN)")
}
Write-Host "Engine: $Mkpfs"
& $Py -m pip install --quiet --upgrade "$Mkpfs"
if ($LASTEXITCODE -ne 0) { throw "could not install mkpfs ($Mkpfs)" }
$Version = (& $Py -c "import sys; sys.path.insert(0, r'$Root'); import ps5_game_packer; print(ps5_game_packer.__version__)")
Write-Host "ps5-game-packer $Version; mkpfs: $(& (Join-Path $Venv 'Scripts\mkpfs.exe') -V)"

# Third-party licenses (they go inside the package)
& $Py tools\licenses.py $Venv
if ($LASTEXITCODE -ne 0) { throw "licenses.py failed" }

# UnRAR.exe from RARLAB (its license allows redistributing it with other programs)
$Unrar = Join-Path $Root "packaging\unrar"
if (-not $NoUnrar -and -not (Test-Path (Join-Path $Unrar "UnRAR.exe"))) {
    New-Item -ItemType Directory -Force -Path $Unrar | Out-Null
    $Sfx = Join-Path $env:TEMP "unrarw64.exe"
    $Extracted = Join-Path $env:TEMP "unrarw64-extracted"
    Write-Host "Downloading UnRAR from rarlab.com"
    Invoke-WebRequest -Uri "https://www.rarlab.com/rar/unrarw64.exe" -OutFile $Sfx
    if (Test-Path $Extracted) { Remove-Item -Recurse -Force $Extracted }
    # It is a WinRAR self-extracting archive: -s (silent) and -d<folder> (destination)
    Start-Process -FilePath $Sfx -ArgumentList @("-s", "-d$Extracted") -Wait
    foreach ($f in @("UnRAR.exe", "license.txt", "unrar.txt", "whatsnew.txt")) {
        $source = Get-ChildItem -Path $Extracted -Recurse -Filter $f | Select-Object -First 1
        if ($source) { Copy-Item $source.FullName (Join-Path $Unrar $f) -Force }
    }
    if (-not (Test-Path (Join-Path $Unrar "UnRAR.exe"))) { throw "UnRAR.exe was not extracted from $Sfx" }
    Write-Host "UnRAR: $(& (Join-Path $Unrar 'UnRAR.exe') | Select-Object -First 2)"
}
if (Test-Path (Join-Path $Unrar "license.txt")) {
    Copy-Item (Join-Path $Unrar "license.txt") (Join-Path $Root "LICENSES\UnRAR-RARLAB.txt") -Force
    & $Py tools\licenses.py $Venv | Out-Null
}

# Tests with the engine and the UnRAR that are going into the package
if (Test-Path (Join-Path $Unrar "UnRAR.exe")) { $env:MKPFS_UNRAR = Join-Path $Unrar "UnRAR.exe" }
$env:PS5_GAME_PACKER_VENV = $Venv
& $Py -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "pytest failed" }
if ($TestsOnly) { Write-Host "Tests green; not building (-TestsOnly)"; exit 0 }

# Build
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
& $Py -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\windows.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# Smoke test: the bundled engine starts and knows about archives and layers
$EngineExe = Join-Path $Root "dist\ps5-game-packer\mkpfs.exe"
& $EngineExe -V
$help = (& $EngineExe pack folder --help | Out-String)
if ($help -notmatch "--layer" -or $help -notmatch "--archive-root") { throw "the bundled mkpfs.exe has no --layer/--archive-root" }

$Zip = Join-Path $Root "dist\ps5-game-packer-$Version-windows-x64.zip"
if (Test-Path $Zip) { Remove-Item -Force $Zip }
Compress-Archive -Path (Join-Path $Root "dist\ps5-game-packer") -DestinationPath $Zip
$MB = [math]::Round((Get-Item $Zip).Length / 1MB, 1)
Write-Host "Done: $Zip ($MB MB)"
