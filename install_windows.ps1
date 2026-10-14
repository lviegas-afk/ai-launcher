# Windows: instala AI Launcher para el usuario actual (sin permisos de administrador).
#  - copia la app a %LOCALAPPDATA%\AI Launcher\app
#  - crea un entorno Python propio en %LOCALAPPDATA%\AI Launcher\venv
#  - agrega accesos directos al menú Inicio (y opcionalmente al Escritorio)
# Uso: doble clic en install_windows.bat, o  powershell -ExecutionPolicy Bypass -File install_windows.ps1 [-Desktop]
param([switch]$Desktop)
$ErrorActionPreference = "Stop"

$AppName = "AI Launcher"
$Src     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dest    = Join-Path $env:LOCALAPPDATA $AppName
$AppDir  = Join-Path $Dest "app"
$Venv    = Join-Path $Dest "venv"
$PyW     = Join-Path $Venv "Scripts\pythonw.exe"
$Py      = Join-Path $Venv "Scripts\python.exe"

function Find-Python {
    # 1) lanzador "py" de python.org  2) python del PATH   (se evita el alias de Microsoft Store)
    $cands = @()
    if (Get-Command py -ErrorAction SilentlyContinue) { $cands += ,@("py", "-3") }
    foreach ($n in "python3", "python") {
        $c = Get-Command $n -ErrorAction SilentlyContinue
        if ($c -and $c.Source -notmatch "WindowsApps") { $cands += ,@($c.Source) }
    }
    foreach ($c in $cands) {
        $exe = $c[0]; $rest = @($c | Select-Object -Skip 1)
        try {
            & $exe @rest -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { return ,$c }
        } catch {}
    }
    return $null
}

Write-Host "-> Buscando Python 3.9+..."
$python = Find-Python
if (-not $python) {
    Write-Host "No encontre Python 3.9 o superior." -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $ans = Read-Host "Instalarlo ahora con winget (Python 3.12)? [s/N]"
        if ($ans -match "^[sSyY]") {
            winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
            $python = Find-Python
        }
    }
    if (-not $python) { throw "Instala Python desde https://www.python.org/downloads/ (marca 'Add python.exe to PATH') y volve a correr este instalador." }
}
$pyExe = $python[0]; $pyArgs = @($python | Select-Object -Skip 1)
Write-Host "   usando: $($python -join ' ')"

# cerrar una instancia anterior
$state = Join-Path $env:APPDATA "$AppName\server.json"
if (Test-Path $state) {
    try {
        $old = (Get-Content $state -Raw | ConvertFrom-Json).pid
        if ($old) { Stop-Process -Id $old -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 1 }
    } catch {}
    Remove-Item $state -ErrorAction SilentlyContinue
}

Write-Host "-> Entorno Python en $Venv"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
if (-not (Test-Path $Py)) { & $pyExe @pyArgs -m venv $Venv }
& $Py -m pip install -q --upgrade pip
& $Py -m pip install -q -r (Join-Path $Src "app\requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "No pude instalar las dependencias (conexion a internet?)" }

Write-Host "-> Copiando la app a $AppDir"
if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir }
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
Copy-Item (Join-Path $Src "app\*.py"), (Join-Path $Src "app\ui.html"), (Join-Path $Src "app\help.js"), (Join-Path $Src "app\requirements.txt") $AppDir

Write-Host "-> Iconos"
& $Py (Join-Path $Src "tools\make_icon.py") (Join-Path $Src "build") | Out-Null
Copy-Item (Join-Path $Src "build\AppIcon.ico"), (Join-Path $Src "build\tray.png") $AppDir

Write-Host "-> Accesos directos"
$shell = New-Object -ComObject WScript.Shell
$targets = @([Environment]::GetFolderPath("Programs"))
if ($Desktop) { $targets += [Environment]::GetFolderPath("Desktop") }
foreach ($dir in $targets) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "$AppName.lnk"))
    $lnk.TargetPath = $PyW
    $lnk.Arguments = "`"$(Join-Path $AppDir 'launcher.py')`""
    $lnk.WorkingDirectory = $AppDir
    $lnk.IconLocation = (Join-Path $AppDir "AppIcon.ico")
    $lnk.Description = "Inicia, monitorea y detiene Ollama y Stable Diffusion"
    $lnk.Save()
}

Write-Host ""
Write-Host "Listo. Busca '$AppName' en el menu Inicio." -ForegroundColor Green
$ans = Read-Host "Abrirlo ahora? [S/n]"
if ($ans -notmatch "^[nN]") { Start-Process $PyW -ArgumentList "`"$(Join-Path $AppDir 'launcher.py')`"" -WorkingDirectory $AppDir }
