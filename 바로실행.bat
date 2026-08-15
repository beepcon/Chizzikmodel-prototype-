@echo off
title Laika
cd /d "%~dp0"
python laika_gui.py
if errorlevel 1 pause
