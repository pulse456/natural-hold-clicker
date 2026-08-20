$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $PythonExe)) {
    $PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($PythonLauncher) {
        & py -3 -m venv (Join-Path $ProjectDir ".venv")
    }
    else {
        $SystemPython = Get-Command python -ErrorAction SilentlyContinue
        if (-not $SystemPython) {
            throw "未找到 Python 3，请先从 python.org 安装。"
        }
        & python -m venv (Join-Path $ProjectDir ".venv")
    }
}

& $PythonExe -m pip install --disable-pip-version-check -r (Join-Path $ProjectDir "requirements-build.txt")

Push-Location $ProjectDir
try {
    & $PythonExe -m PyInstaller --noconfirm --clean --onefile --windowed `
        --name "自然长按连点器" `
        --add-data "$ProjectDir\assets;assets" `
        --hidden-import dxcam `
        --hidden-import mss `
        "$ProjectDir\human_clicker.py"
    Write-Host "已生成: $ProjectDir\dist\自然长按连点器.exe"
}
finally {
    Pop-Location
}
