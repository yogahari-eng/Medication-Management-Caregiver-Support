CARELINK - SENIOR SAFETY APP (full package)

FOLDERS
  server\   Python backend + web app UI  (the brain: IVR, SOS, caretaker ranking, door, ambulance)
  android\  Android Studio project       (the phone app: opens the UI, GPS, system notifications)

RUN (2 steps)
 1. Double-click START_SERVER.bat   (keep the window open; first run installs packages)
 2. Android Studio > Open > select the "android" folder > wait for Gradle sync > pick emulator > Run

EMULATOR: works as-is (http://10.0.2.2:5000).
REAL PHONE: same Wi-Fi as PC, allow port 5000 in Windows Firewall, then on the login screen tap
            "Server settings" and enter http://<PC-IP>:5000 (IP is printed by START_SERVER.bat).
GPS: emulator -> ... > Location > Send. The app reads GPS natively (no HTTPS needed).
TWO ROLES: register one account as Caregiver and another as Caretaker (second emulator or phone,
           or Chrome on the PC at http://localhost:5000). Use the Demo tab to trigger the flow.

NOT INCLUDED: real phone calls (needs Twilio/Exotel keys), FCM background push, real ambulance/door
hardware. See server\README.md for the integration hooks.
