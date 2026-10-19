@echo off
rem Runs the bot in the background on Windows (no console window).
rem Put a shortcut to this file in shell:startup, or add it to Task Scheduler "At log on".
cd /d "%~dp0.."
start "" ".venv\Scripts\pythonw.exe" -m sniper_bot -c config.toml --log-file sniper.log run
