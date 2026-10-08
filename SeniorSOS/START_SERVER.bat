@echo off
cd /d "%~dp0server"
if not exist venv py -m venv venv
call venv\Scripts\activate
pip install flask flask-sqlalchemy pyjwt
echo.
echo ===== Emulator address : http://10.0.2.2:5000
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo ===== Real phone address, PC IP:%%a  (use http://IP:5000)
echo.
python app.py
pause
