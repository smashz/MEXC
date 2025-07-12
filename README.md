# MEXC⚡
MEXC⚡: High-Performance Crypto Trading Bot for MEXC Exchange

---

## Usage

1. **Install the required libraries**

   ```bash
   pip install -r requirements.txt
   ```

2. **Run the MEXC API Test**

   If you're using a virtual environment (e.g., conda), activate it first:
   
   ```bash
   conda activate your_environment_name
   ```

   Then, run the script:
   
   ```bash
   python test_api.py
   ```

3. **Run the script to find tradable pairs**

   Execute the PowerShell script below to generate a `tradable_pairs.txt` file.
   This file will list all symbols that support **spot trading** on the MEXC exchange.

   ```powershell
   .\find_tradeables.ps1
   ```

