@echo off
title GasBenchmark A.I. Watchdog - Port 5026
cd /d C:\Users\abc\Desktop\GasBenchmarkAI
start /min "GasBenchmark A.I. Engine" cmd /c C:\Users\abc\AppData\Local\Programs\Python\Python313\python.exe watchdog_gasbenchmark.py
