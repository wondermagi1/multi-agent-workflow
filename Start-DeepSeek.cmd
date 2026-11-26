@echo off
setlocal
pushd "%~dp0" || exit /b 1
set "WORKFLOW_PORT=%~1"
set "WORKFLOW_CONFIGURE="
if /i "%WORKFLOW_PORT%"=="--configure" (
    set "WORKFLOW_CONFIGURE=1"
    set "WORKFLOW_PORT="
)
if not defined WORKFLOW_PORT set "WORKFLOW_PORT=8765"
powershell.exe -NoProfile -Command ^
 "$ErrorActionPreference = 'Stop';" ^
 "$credentialDirectory = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Codex\.multi-agent-workflow';" ^
 "$credentialFile = Join-Path $credentialDirectory 'deepseek-key.xml';" ^
 "if ($env:WORKFLOW_CONFIGURE -eq '1' -or -not (Test-Path -LiteralPath $credentialFile -PathType Leaf)) {" ^
 "    $newKey = Read-Host 'DeepSeek API Key' -AsSecureString;" ^
 "    try {" ^
 "        if ($newKey.Length -eq 0) { throw 'The DeepSeek API key is empty.' }" ^
 "        [void][System.IO.Directory]::CreateDirectory($credentialDirectory);" ^
 "        Export-Clixml -InputObject $newKey -LiteralPath $credentialFile" ^
 "    } finally { $newKey.Dispose() }" ^
 "    if ($env:WORKFLOW_CONFIGURE -eq '1') { Write-Host 'DeepSeek key saved.'; exit 0 }" ^
 "}" ^
 "$encryptedKey = Import-Clixml -LiteralPath $credentialFile;" ^
 "try {" ^
 "    $env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new('', $encryptedKey).Password;" ^
 "    & python -X utf8 app.py --provider deepseek --port $env:WORKFLOW_PORT;" ^
 "    exit $LASTEXITCODE" ^
 "} finally {" ^
 "    Remove-Item Env:DEEPSEEK_API_KEY -ErrorAction SilentlyContinue;" ^
 "    $encryptedKey.Dispose()" ^
 "}"
set "WORKFLOW_EXIT_CODE=%ERRORLEVEL%"
popd
if not "%WORKFLOW_EXIT_CODE%"=="0" if "%~1"=="" pause
exit /b %WORKFLOW_EXIT_CODE%
