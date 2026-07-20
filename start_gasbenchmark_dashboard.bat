@echo off
title GasBenchmark A.I. Dashboard - Port 5026
cd /d C:\Users\abc\Desktop\GasBenchmarkAI
start /min "GasBenchmark A.I. Dashboard" cmd /c C:\Users\abc\AppData\Local\Programs\Python\Python313\python.exe dashboard_gasbenchmark.py
timeout /t 5 /nobreak >nul
start http://localhost:5026
