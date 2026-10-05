$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

try {
    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host 'Preparation de Python (premier lancement)...'
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
            if ($LASTEXITCODE -ne 0) { throw 'Installez Python 3.12 ou plus recent.' }
            & py -3 -m venv .venv
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            & python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
            if ($LASTEXITCODE -ne 0) { throw 'Installez Python 3.12 ou plus recent.' }
            & python -m venv .venv
        } else {
            throw 'Python est introuvable. Installez Python 3.12+ depuis python.org.'
        }
        if ($LASTEXITCODE -ne 0) { throw 'Impossible de creer l environnement Python.' }
    }

    $requirementsPath = Join-Path $PSScriptRoot 'requirements.txt'
    $requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash
    $markerPath = Join-Path $PSScriptRoot '.venv\requirements.sha256'
    $installedHash = if (Test-Path -LiteralPath $markerPath) {
        (Get-Content -LiteralPath $markerPath -Raw).Trim()
    } else { '' }
    if ($installedHash -ne $requirementsHash) {
        Write-Host 'Installation des dependances Python (aucun modele Ollama telecharge)...'
        & $venvPython -m pip install -r $requirementsPath
        if ($LASTEXITCODE -ne 0) { throw 'Installation interrompue. Verifiez la connexion Internet.' }
        Set-Content -LiteralPath $markerPath -Value $requirementsHash -Encoding ASCII
    }

    Write-Host 'Ouverture de l application : http://localhost:8501'
    Write-Host 'Gardez cette fenetre ouverte. Ctrl+C pour fermer le serveur.'
    & $venvPython -m streamlit run app.py --server.headless=false
    if ($LASTEXITCODE -ne 0) { throw 'Le serveur Streamlit s est arrete avec une erreur.' }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
