@echo off
echo Installation Face Finder...
pip install -r requirements.txt
echo.
echo Lance avec: python app.py
echo Build exe: pyinstaller --onefile --windowed --name FaceFinder app.py
pause
