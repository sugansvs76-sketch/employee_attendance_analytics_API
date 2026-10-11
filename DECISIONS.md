# DECISIONS.md

1. Indexes. I create the unique employee code index and the attendance composite index on `(emp_code, date)`, plus the date and employee ordering indexes used by listing and monthly analytics. I also keep the status/date and date/late indexes for the reporting queries. I considered a broad department+date index, but it would not add much to the current access patterns and would increase writes without materially reducing the cost of the month-scoped reports.

2. Punch-in race. If two identical punch-in requests hit at the same instant, the first insert succeeds and creates the unique `(emp_code, date)` attendance record. The second insert hits the same unique key, raises `DuplicateKeyError`, and the API converts it to a 409. This preserves the invariant that there is at most one attendance document per employee per attendance date.

3. Ties. The leaderboard uses a competition rank: all employees at the same total late minutes receive the same rank, and the next rank is skipped. The filter is applied after ranking, so if two employees tie at the cutoff rank, both are returned because they both satisfy `rank <= limit`. Ordering is by `total_late_minutes` descending then `emp_code` ascending for deterministic output.

4. Headcount. The department summary starts from the `employees` collection and filters only `joined_on <= end_of_month`. That means employees with zero logs are still present in the headcount. The lookup to `attendance_logs` is optional and only aggregates the records they actually have, so zero-log employees are included without requiring a fake document.

5. One thing I would change for 100x the data. I would move the heavy reporting work out of the request path and into a materialized summary table or scheduled aggregation job, because the department and trend reports are read-heavy and benefit from precomputed daily totals instead of re-aggregating the entire dataset every time.
