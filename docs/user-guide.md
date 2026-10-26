# Attendance App User Guide

Open `http://localhost:8000/` to use Roll Call in a browser.

## Students

1. Sign in with your university account.
2. When your professor starts attendance, a yellow card appears on your home
   page within a few seconds.
3. Type the code from the board (spaces and lowercase are fine), or scan the
   QR on the board with your phone camera. The QR opens Roll Call and marks
   you present.
4. If location is requested, allow location access while in the classroom.
5. Wait for **You're marked present** (or **late**). Typing a code is not
   enough on its own; the message confirms the server accepted it.
6. **My attendance** shows your percentage and each class as present, late,
   excused, or absent. A class still taking attendance isn't counted yet.
7. To be excused, use **Absence requests**: pick the class and the day you
   missed, write a reason, and optionally attach a PDF or photo (up to 5 MB).
   If your professor approves it, that class no longer counts against you.
8. Check **Notifications** for cancelled classes and absence-request decisions.

## Professors

1. Create an account with the professor access code from your department, then
   add a class with **Add class**.
2. On the **Students** tab, add students by email. You can paste several,
   separated by commas. **Remove** takes a student out of the class but keeps
   their past attendance.
3. Select **Start attendance** and choose how long it stays open. Optionally
   mark students **late** if they confirm after 5, 10, or 15 minutes, and turn
   on **Only count students who are in the room**.
4. The attendance board opens. Show it on the projector (**Full screen**). It
   shows the code, a QR that changes every 20 seconds, the time left, and who
   is present. Use **New code** if the code was shared outside the room and
   **End attendance** to stop early. If you close the board, **Show attendance
   board** brings it back with a new code.
5. **Cancel class** if the class isn't happening. Students are notified and the
   session doesn't count.
6. Open a session's **Attendance list** to correct a status by hand, for
   example for a student whose phone was dead. The student is notified.
7. On **Absence requests**, open any attached document and approve or reject.
   Approving a request for a class excuses the student from it.
8. **Statistics** shows attendance per finished session and overall.

## Important rules

- Each student can confirm attendance only once per session.
- Codes and QR tokens expire and must not be shared.
- Attendance is confirmed only after a successful server response.
- Location is used only when the professor enables it for that session.
- Do not share passwords, access tokens, codes, or QR screenshots.
