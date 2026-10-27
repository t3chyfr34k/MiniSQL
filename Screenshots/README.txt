Floating SQL Assistant walkthrough

Files
- 00-intro.png: intro screen
- 01-ask.png: Ask page
- 02-sql.png: SQL page
- 03-setup.png: Setup page
- intro-recording.gif: animated intro capture
- usage-demo.gif: intro and a short UI walkthrough

The walkthrough types an example request and selects Generate, which opens
Setup because no database schema was loaded. It stops before connecting to
SQL Server or contacting an AI provider.

To use the app
1. Run `python app.py`.
2. In Setup, confirm the server, database, driver, and authentication, then
   choose Connect / refresh.
3. In Ask, describe the query you need and choose Basic SQL, CTE, Joins, or
   Aggregation. Select Generate (or press Ctrl+Enter).
4. Review and edit the result on the SQL page. Copy SQL, then paste it into
   the intended SSMS query window.

The app does not execute SQL. Review generated SQL before running it in SSMS.
