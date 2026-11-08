@echo off
cd /d "D:\programmanig project\Asan clinic forms and logo\Asan_Invoice_maker_bot"
start "" /B python -u invoice_bot.py >> "%~dp0bot.log" 2>&1
