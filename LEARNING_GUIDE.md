# Learning guide

## Run the project on macOS

1. Install Python 3.12 from [Python.org](https://www.python.org/downloads/macos/) and open Terminal. Run `python3.12 --version` to confirm that Terminal can find it.
2. Clone the repository and enter it. If you already have a checkout, start in that directory. GitHub can also provide a downloadable ZIP.

   ```bash
   git clone https://github.com/mus2325/nyc-urban-data-intelligence.git
   cd nyc-urban-data-intelligence
   ```

   To try the branch during review, run `git fetch origin` and `git switch --track origin/feat/premium-dashboard` once the branch is on GitHub.

3. Create and activate an isolated environment, then install the libraries.

   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   ```

   The virtual environment gives this project its own packages. Activate it when you open a new Terminal window. Use `deactivate` when you finish.

4. Try a single-page live check with `python -m nyc311.etl --validate-page --page-size 50`. This prints quality counts without opening your dashboard database. For a check against a known historical window, follow the [README example](README.md#validate-a-live-page-or-historical-window). Then run a full ingestion to populate the dashboard.

   ```bash
   python -m nyc311.etl --page-size 50
   python -m streamlit run app.py --browser.gatherUsageStats false
   ```

   Open [localhost:8501](http://localhost:8501), select **Request explorer**, and pick a borough. Check how the charts and request count respond. Press **Control+C** to stop Streamlit.

5. Run the automated tests from the repository root.

   ```bash
   python -m unittest discover -v
   ```

## Follow a request through the pipeline

1. **Extract.** An API is an interface programs can call over the network. Requests sends an HTTPS request to NYC Open Data. Each query asks for the dashboard fields and the system modification timestamp. The page size caps how many records each request returns. The response is JSON, which Python reads as a list of dictionaries.
2. **Transform.** Pandas turns the dictionaries into a DataFrame, a table with named columns. The code trims text, parses local NYC timestamps, and rejects rows with unusable IDs, dates, or categories. It labels missing or unrecognized boroughs as `UNKNOWN` so those records still count.
3. **Stage and load.** The pipeline writes each cleaned page to a temporary SQLite file. This is a work area on disk that holds the newest valid version of each ID. Even thousands of rows sharing a timestamp are cleaned one page at a time. Once every page succeeds, the pipeline streams staged rows into the dashboard database. The primary key makes each request ID unique even across multiple ETL runs. An upsert inserts a new ID or changes the stored version of an existing ID. A transaction saves the rows and checkpoint together. Replaying the same source version leaves the dashboard records unchanged, which is an example of idempotence.
4. **Explore.** Streamlit reads the database and reruns the dashboard code when you change a filter. Pandas counts categories and boroughs and groups requests by day. The dashboard uses a read-only connection, so filtering has no effect on stored data.

Logs explain what each ETL run did. Errors produce a nonzero exit code that a shell or future scheduler can detect. Git ignores the virtual environment and generated database because those are local artifacts.

## Read the dashboard like an analyst

The overview shows the size and date range of your local sample. Explorer filters change the requests included in the charts and table. For example, the share for a complaint category is its number of stored requests divided by the number of requests in the current chart selection.

Creation time describes when people opened requests. The latest stored source update describes when NYC Open Data last modified one of your stored rows. The pipeline's last successful run tells you when your ETL finished, and its checkpoint tells you how far it scanned. These can differ when a run checks a historical window or finds no changes.

The Insights section compares two weeks only when both have stored requests on all seven dates and enough rows for the dashboard's minimum-count rule. It excludes the first and last observed dates, which can be partial. A difference describes the collection. The dashboard has no way to tell whether an unobserved date had zero NYC requests, and the week comparison does not test statistical significance.

## How the next run knows where to start

Think of the watermark as the end time of the last fully scanned window. SQLite stores it as a checkpoint. Each run re-reads the 10 minutes before that time so a recently delayed row can still be collected. The scan uses the source's modification time to catch edits to older requests. The dashboard still uses creation time for its trends.

Pagination splits large responses into pages. If the previous page ended at time 10:05 with ID 250, the next page can include IDs greater than 250 at 10:05, as well as requests modified later. Saving the checkpoint only after all pages are read and written lets a failed run retry from its last successful checkpoint. When you stop a scan with **Control+C** or a page fails, the temporary staging file is cleaned up and the next run fetches those pages again.

The first run has no checkpoint, so it starts one hour before the command runs. To retry a first run from the same starting point, choose a time with `--since` and reuse it until that run succeeds. For example, `--since 2026-11-19T21:00:00Z` selects 21:00 UTC. Omit this flag once there is a checkpoint. Each later run advances by at most a day, so catching up after a long break can take several runs.

## Inspect the database

macOS typically provides the `sqlite3` command. After the ETL succeeds, run this from the repository root.

```bash
sqlite3 -readonly data/nyc311.sqlite3 'SELECT borough, COUNT(*) FROM requests GROUP BY borough ORDER BY COUNT(*) DESC;'
```

Run the ETL again, then repeat the query. Recent requests may have arrived or changed in the meantime. Compare the new, updated, and unchanged counts in the ETL log.

If the dashboard says the database is missing, run the ETL first and check its output. If you see a proxy or connection error, check that your network allows access to `data.cityofnewyork.us`. You can still run the offline tests with installed dependencies.

To inspect the checkpoint and the last successful run's quality counts, use:

```bash
sqlite3 -readonly data/nyc311.sqlite3 'SELECT watermark, last_successful_run, quality_counts FROM ingestion_state;'
```

## Relate the pipeline to AWS Cloud Practitioner concepts

Run and study this project locally with public NYC Open Data access. These examples explain how common AWS services divide the same responsibilities.

- **Lambda** provides serverless compute. An ETL handler could run for each scheduled ingestion.
- **EventBridge Scheduler** can invoke that handler on a recurring schedule.
- **S3** is object storage. A future version could archive the original API pages so a failed transformation can be replayed.
- **RDS** provides managed relational databases. An RDS database could hold the records and checkpoint together when the dashboard and ETL run on different machines.
- **IAM** controls access. An IAM role could grant the ETL only the permissions needed for its archive and database.
- **CloudWatch** collects logs and metrics. A cloud ETL could log rejected counts and use an alarm to flag a missed successful run.

## See your tests run on GitHub

Continuous integration (CI) runs automated checks as you develop. The [CI workflow](.github/workflows/ci.yml) runs when a pull request targeting `main` is opened, reopened, or updated with new commits. GitHub starts a fresh Ubuntu runner, checks out the proposed code, sets up Python 3.12, installs `requirements.txt`, and runs the same `python -m unittest discover -v` command you use locally.

The tests simulate NYC Open Data responses and use temporary SQLite files. You can run CI without configuring an API key, repository secret, AWS account, or paid service.

On GitHub, open the pull request and select **Checks** to see the **Python tests** job. Expand **Run tests** to see each result. If the job fails, open the failed step to see the error, fix it locally, and push another commit to the pull request branch. You can also find the run under the repository's **Actions** tab by selecting **CI**.
