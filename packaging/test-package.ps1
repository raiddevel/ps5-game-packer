# End-to-end test of the built Windows package (the zip from packaging\windows.ps1): it unpacks it into a clean
# folder, creates the synthetic game (folder, multi-volume RAR with a password both loose and inside a subfolder,
# base + update + backport + DLC, and a base with a broken param.json plus a folder with the good one), packs each
# case with ps5-game-packer.exe --auto, unpacks with mkpfs.exe --deep and compares byte by byte (SHA-256) with
# what is expected.
#
#   powershell -ExecutionPolicy Bypass -File packaging\test-package.ps1 -Zip dist\ps5-game-packer-2.1.3-windows-x64.zip
#
# Parameters: -Zip <zip> (required)  -Folder <test folder> (default %TEMP%\ps5-game-packer-test)
#             -Python <python.exe> (creates the synthetic game; default "python")
#             -Rar <WinRAR's Rar.exe> (for the RAR case; default C:\Program Files\WinRAR\Rar.exe; skipped if missing)
param(
    [Parameter(Mandatory = $true)][string]$Zip,
    [string]$Folder = (Join-Path $env:TEMP "ps5-game-packer-test"),
    [string]$Python = "python",
    [string]$Rar = "C:\Program Files\WinRAR\Rar.exe"
)
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Failures = 0

function Step([string]$text) { Write-Host "" ; Write-Host "== $text" }
function Good([string]$text) { Write-Host "   OK   $text" }
function Bad([string]$text) { Write-Host "   FAIL $text"; $script:Failures++ }

function Hashes([string]$root) {
    $table = @{}
    Get-ChildItem -Path $root -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($root.Length).TrimStart("\").Replace("\", "/")
        $table[$rel] = (Get-FileHash -Algorithm SHA256 $_.FullName).Hash
    }
    return $table
}

function Compare-Trees([hashtable]$seen, [hashtable]$expected, [string]$name) {
    $s = @{} + $seen
    $s.Remove("ampr_emu.index") | Out-Null
    $missing = @($expected.Keys | Where-Object { -not $s.ContainsKey($_) })
    $extra = @($s.Keys | Where-Object { -not $expected.ContainsKey($_) })
    $different = @($expected.Keys | Where-Object { $s.ContainsKey($_) -and $s[$_] -ne $expected[$_] })
    if ($missing.Count -eq 0 -and $extra.Count -eq 0 -and $different.Count -eq 0) {
        Good "$name - $($expected.Count) files identical byte by byte"
    } else {
        Bad "$name - missing: $($missing -join ', ') | extra: $($extra -join ', ') | different: $($different -join ', ')"
    }
}

function Pack([string]$source, [string]$output, [string]$password = "", [bool]$mustFail = $false) {
    New-Item -ItemType Directory -Force -Path $output | Out-Null
    Get-ChildItem -Path $output -Filter "*.ffpfsc*" | Remove-Item -Force
    if ($password) { $env:PS5_GAME_PACKER_PASSWORD = $password } else { Remove-Item Env:\PS5_GAME_PACKER_PASSWORD -ErrorAction SilentlyContinue }
    $t0 = Get-Date
    $log = Join-Path $output "log.txt"
    $p = Start-Process -FilePath $Exe -ArgumentList @("--lang", "en", "--auto", "`"$source`"", "`"$output`"", "--start", "--quit-when-done", "--log", "`"$log`"") -PassThru
    if (-not $p.WaitForExit(600000)) { $p.Kill(); Bad "ps5-game-packer.exe did not finish in 10 min ($source)"; return $null }
    $sec = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
    if ($mustFail) {
        $made = Get-ChildItem -Path $output -Filter "*.ffpfsc" | Select-Object -First 1
        if (Test-Path $log) { Get-Content $log | ForEach-Object { Write-Host "        | $_" } }
        if ($p.ExitCode -ne 0 -and -not $made) { Good "exits with code $($p.ExitCode) and no container, as it should ($sec s)"; return $null }
        Bad "it had to fail and exited with code $($p.ExitCode) (container: $made)"; return "bad"
    }
    if ($p.ExitCode -ne 0) {
        Bad "ps5-game-packer.exe exited with code $($p.ExitCode) ($source)"
        if (Test-Path $log) { Get-Content $log | ForEach-Object { Write-Host "        | $_" } }
        return $null
    }
    $made = Get-ChildItem -Path $output -Filter "*.ffpfsc" | Select-Object -First 1
    if (-not $made) { Bad "no .ffpfsc was created in $output ($sec s)"; return $null }
    $out = $made.FullName
    Good "container created in $sec s: $($made.Name) ($([math]::Round($made.Length / 1MB, 1)) MB)"
    return $out
}

function Unpack([string]$container, [string]$target) {
    if (Test-Path $target) { Remove-Item -Recurse -Force $target }
    # Start-Process: mkpfs.exe writes the progress bar to stderr and PowerShell 5.1 would take it for an error
    $log = Join-Path $Folder "unpack.log"
    $err = Join-Path $Folder "unpack.err"
    $p = Start-Process -FilePath $Engine -ArgumentList @("unpack", "--deep", "`"$container`"", "`"$target`"") -Wait -PassThru -NoNewWindow -RedirectStandardOutput $log -RedirectStandardError $err
    if ($p.ExitCode -ne 0) { Bad "mkpfs.exe unpack failed ($container)"; Get-Content $err -Tail 5; return $false }
    return $true
}

# 0. install the zip in a clean folder
Step "Install $Zip into $Folder"
if (Test-Path $Folder) { Remove-Item -Recurse -Force $Folder }
New-Item -ItemType Directory -Force -Path $Folder | Out-Null
Expand-Archive -Path $Zip -DestinationPath (Join-Path $Folder "app")
$App = Get-ChildItem -Path (Join-Path $Folder "app") -Directory | Select-Object -First 1
$Exe = Join-Path $App.FullName "ps5-game-packer.exe"
$Engine = Join-Path $App.FullName "mkpfs.exe"
if (-not (Test-Path $Exe)) { throw "there is no ps5-game-packer.exe in the zip" }
Good "exe: $Exe"
$v = (cmd /c "`"$Engine`" -V 2>&1" | Out-String).Trim()
Good "mkpfs.exe: $v"
$help = (cmd /c "`"$Engine`" pack folder --help 2>&1" | Out-String)
if ($help -match "--layer" -and $help -match "--archive-root") { Good "mkpfs.exe knows --archive-root and --layer" } else { Bad "mkpfs.exe lacks --archive-root/--layer" }
$unrar = Join-Path $App.FullName "_internal\UnRAR.exe"
if (Test-Path $unrar) { Good "UnRAR.exe included: $((cmd /c "`"$unrar`" 2>&1" | Select-Object -First 2 | Out-String).Trim())" } else { Bad "no UnRAR.exe in _internal" }

# 1. synthetic game
Step "Synthetic game"
$Games = Join-Path $Folder "games"
& $Python (Join-Path $Repo "tests\synthetic_game.py") $Games --ampr | Out-Null
$Base = Get-ChildItem -Path $Games -Directory | Where-Object { $_.Name -eq "Sample.Game.PPSA99999" } | Select-Object -First 1
if (-not $Base) { throw "the synthetic game was not created (python: $Python)" }
Good "base: $($Base.FullName)"
# expected merged view: base + update + backport + dlc (the top one wins)
$Merged = Join-Path $Folder "expected-merged"
Copy-Item -Recurse $Base.FullName $Merged
foreach ($layer in @("Sample.Game.Update.v1.05.PPSA99999", "Sample.Game.v1.05.Backport.5.50.PPSA99999")) {
    Copy-Item -Recurse -Force (Join-Path $Games "$layer\*") $Merged
}
$DlcTmp = Join-Path $Folder "dlc-tmp"
Expand-Archive -Path (Join-Path $Games "Sample.Game.DLC.Pack.PPSA99999.zip") -DestinationPath $DlcTmp
Copy-Item -Recurse -Force (Join-Path $DlcTmp "PPSA99999-app\*") $Merged
$HashBase = Hashes $Base.FullName
$HashMerged = Hashes $Merged

# 2. folder
Step "Folder -> .ffpfsc"
$out = Pack $Base.FullName (Join-Path $Folder "out-folder")
if ($out -and (Unpack $out (Join-Path $Folder "unpack-folder"))) {
    $seen = Hashes (Join-Path $Folder "unpack-folder")
    if ($seen.ContainsKey("ampr_emu.index")) { Good "ampr_emu.index generated" } else { Bad "ampr_emu.index missing" }
    Compare-Trees $seen $HashBase "folder"
}

# 3. multi-volume RAR with a password
Step "Multi-volume RAR with a password -> .ffpfsc (without extracting)"
if (Test-Path $Rar) {
    $RarDir = Join-Path $Folder "rar"
    New-Item -ItemType Directory -Force -Path $RarDir | Out-Null
    Copy-Item -Recurse $Base.FullName (Join-Path $RarDir "PPSA99999-app")
    Push-Location $RarDir
    # RAR appends .partN to the base name: Sample.Game.PPSA99999.part1.rar, .part2.rar...
    cmd /c "`"$Rar`" a -r -ep1 -m3 -v2m -pSecreto123 -idq Sample.Game.PPSA99999.rar PPSA99999-app 2>&1" | Out-Null
    Pop-Location
    Remove-Item -Recurse -Force (Join-Path $RarDir "PPSA99999-app")
    $parts = @(Get-ChildItem -Path $RarDir -Filter "*.rar").Count
    Good "RAR created in $parts volumes"
    $out = Pack (Join-Path $RarDir "Sample.Game.PPSA99999.part1.rar") (Join-Path $Folder "out-rar") "Secreto123"
    if ($out -and (Unpack $out (Join-Path $Folder "unpack-rar"))) {
        Compare-Trees (Hashes (Join-Path $Folder "unpack-rar")) $HashBase "multi-volume rar + password"
    }
    Step "RAR with a wrong password: it has to fail"
    Pack (Join-Path $RarDir "Sample.Game.PPSA99999.part1.rar") (Join-Path $Folder "out-rar-wrong") "wrong" $true | Out-Null
    # 3b. the same multi-volume RAR inside its game folder, choosing the folder above (folder mode, general
    # password): it is scanned as if the archive were loose
    Step "Folder with the multi-volume RAR (password) inside a subfolder -> .ffpfsc"
    $RarSub = Join-Path $Folder "rar-sub"
    New-Item -ItemType Directory -Force -Path (Join-Path $RarSub "Game-PPSA99999-PS5") | Out-Null
    Copy-Item (Join-Path $RarDir "*.rar") (Join-Path $RarSub "Game-PPSA99999-PS5")
    Set-Content -Path (Join-Path $RarSub "Game-PPSA99999-PS5\readme.nfo") -Value "notes"
    $out = Pack $RarSub (Join-Path $Folder "out-rar-sub") "Secreto123"
    if ($out -and (Unpack $out (Join-Path $Folder "unpack-rar-sub"))) {
        Compare-Trees (Hashes (Join-Path $Folder "unpack-rar-sub")) $HashBase "multi-volume rar inside a subfolder"
    }
} else {
    Write-Host "   (no Rar.exe at ${Rar}: RAR case skipped)"
}

# 4. layers
Step "Base + update + backport + DLC (zip) -> one merged .ffpfsc"
$out = Pack $Games (Join-Path $Folder "out-layers")
if ($out -and (Unpack $out (Join-Path $Folder "unpack-layers"))) {
    $seen = Hashes (Join-Path $Folder "unpack-layers")
    Compare-Trees $seen $HashMerged "merge of 4 layers"
    $eboot = [System.IO.File]::ReadAllBytes((Join-Path $Folder "unpack-layers\eboot.bin"))[0..23]
    $text = [System.Text.Encoding]::ASCII.GetString($eboot)
    if ($text -like "ELF-synthetic-backport*") { Good "the final eboot.bin is the backport's (top layer with an eboot)" } else { Bad "unexpected final eboot.bin: $text" }
    if ($seen.ContainsKey("ampr_emu.index")) { Good "ampr_emu.index generated from the merged view" } else { Bad "ampr_emu.index missing" }
}

# 4b. broken param.json replaced by a layer
Step "Base with a broken param.json + folder with only sce_sys/param.json -> the container carries the good one"
$Broken = Join-Path $Folder "param-broken"
New-Item -ItemType Directory -Force -Path (Join-Path $Broken "param.PPSA99999\sce_sys") | Out-Null
Copy-Item -Recurse $Base.FullName (Join-Path $Broken "Game.Broken.PPSA99999")
[System.IO.File]::WriteAllBytes((Join-Path $Broken "Game.Broken.PPSA99999\sce_sys\param.json"), [System.Text.Encoding]::ASCII.GetBytes("{ this is not JSON"))
Copy-Item (Join-Path $Base.FullName "sce_sys\param.json") (Join-Path $Broken "param.PPSA99999\sce_sys\param.json")
$out = Pack $Broken (Join-Path $Folder "out-param")
if ($out) {
    $name = Split-Path -Leaf $out
    if ($name -eq "Sample Game [PPSA99999].ffpfsc") { Good "the container is named after the title of the good param.json" } else { Bad "unexpected name: $name (it had to come from the layer's param.json)" }
    if (Unpack $out (Join-Path $Folder "unpack-param")) {
        Compare-Trees (Hashes (Join-Path $Folder "unpack-param")) $HashBase "broken param.json replaced by the layer (good param.json byte by byte)"
    }
}

# 5. Spanish
Step "Start in Spanish (--lang es, without starting) and close"
$p = Start-Process -FilePath $Exe -ArgumentList @("--lang", "es", "--auto", "`"$($Base.FullName)`"", "`"$(Join-Path $Folder 'out-es')`"") -PassThru
Start-Sleep -Seconds 8
if ($p.HasExited) { Bad "the Spanish window closed by itself (code $($p.ExitCode))" } else { Good "the Spanish window is still open after 8 s"; Stop-Process -Id $p.Id -Force }

Write-Host ""
if ($Failures -eq 0) { Write-Host "ALL OK"; exit 0 } else { Write-Host "$Failures FAILURE(S)"; exit 1 }
