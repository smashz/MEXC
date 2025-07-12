# Set console and output encoding to UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

# Run the Python command with proper encoding
Write-Host "Starting tradeable pairs search..." -ForegroundColor Green
python main.py --action find-tradeable | Out-File -FilePath "tradeable_pairs.txt" -Encoding UTF8
Write-Host "Results saved to tradeable_pairs.txt" -ForegroundColor Green

# Display results
# Write-Host "`nDisplaying results:" -ForegroundColor Yellow
# Get-Content "tradeable_pairs.txt" -Encoding UTF8 
