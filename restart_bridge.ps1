# Restart bridge with explicit proxy env vars
$env:HTTPS_PROXY = "http://127.0.0.1:7890"
$env:HTTP_PROXY = "http://127.0.0.1:7890"
$env:NO_PROXY = "127.0.0.1,localhost"

# Kill existing bridge
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
    $_.CommandLine -like '*bridge_any_llm*'
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1

# Start new bridge
Start-Process -FilePath "C:\Python314\python.exe" `
    -ArgumentList "-u","bridge_any_llm.py" `
    -WorkingDirectory "C:\Users\Administrator\Desktop\Tidal_Echo\examples" `
    -RedirectStandardOutput "C:\Users\Administrator\Desktop\Tidal_Echo\bridge_out.log" `
    -RedirectStandardError "C:\Users\Administrator\Desktop\Tidal_Echo\bridge_err.log" `
    -NoNewWindow

Write-Host "Bridge restarted with proxy=$env:HTTPS_PROXY"
