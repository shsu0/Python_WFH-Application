# Python_WFH-Application
Python application to generate emails for clocking in/out when working from home.

This application was developed with the assistance of Claude Sonnet 5.5.

This application includes the following main features:

Live clock with a 12-hour / 24-hour toggle
Working-hours timer (time since clock-in minus breaks)
Emails are generated ONLY on Clock IN and Clock OUT. Breaks are just recorded and reported in the clock-out email.
Times in emails are rounded to the nearest 15 minutes, and total hours worked are shown as a decimal (e.g. 7.75 hrs). The live on-screen timer stays exact.
Task list: planned task + status/notes + "complete" checkbox per row. In emails, a ticked task shows "Status: Completed" (no checkbox).
Email preview (To, Subject and Body are all editable), then open it in your email app or copy it. The greeting uses the recipient's name. "Update email preview" rebuilds it from your current tasks/details.
Today's session is auto-saved, so closing the app by accident is safe. Once you've clocked out, closing the window resets the day automatically (the next launch starts clean).
