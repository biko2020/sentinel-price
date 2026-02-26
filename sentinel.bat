@echo off
:: =============================================================================
::  SentinelPrice · Windows Automation Entry Point
:: =============================================================================
::  Runs the full pipeline in one command:
::    1. Starts the stack (db + scraper)
::    2. Crawls Amazon and Walmart
::    3. Queries and displays the latest prices
::
::  Usage:
::    Double-click sentinel.bat
::    or from terminal: .\sentinel.bat
::
::  Options:
::    .\sentinel.bat amazon     — crawl Amazon only
::    .\sentinel.bat walmart    — crawl Walmart only
::    .\sentinel.bat query      — query results only (no crawl)
::    .\sentinel.bat reset      — wipe all data and restart fresh
:: =============================================================================

setlocal EnableDelayedExpansion

:: --- Config ------------------------------------------------------------------
set PROJECT_NAME=SentinelPrice
set DB_USER=sentinel_user
set DB_NAME=sentinelprice
set QUERY=SELECT product_name, brand, price_current, currency, availability, scraped_at FROM latest_prices ORDER BY scraped_at DESC LIMIT 20;

:: --- Colors (Windows 10+) ---------------------------------------------------
set RED=[91m
set GREEN=[92m
set YELLOW=[93m
set CYAN=[96m
set RESET=[0m


:: =============================================================================
::  ENTRY POINT
:: =============================================================================

echo.
echo %CYAN%============================================================%RESET%
echo %CYAN%  %PROJECT_NAME% · Automation Entry Point%RESET%
echo %CYAN%============================================================%RESET%
echo.

:: Route to sub-command if argument provided
if "%1"=="amazon"  goto :crawl_amazon
if "%1"=="walmart" goto :crawl_walmart
if "%1"=="query"   goto :query
if "%1"=="reset"   goto :reset


:: =============================================================================
::  DEFAULT: full run (start stack → crawl all → query)
:: =============================================================================

:full_run
echo %YELLOW%[1/4] Starting Docker stack...%RESET%
docker-compose up -d db
if %ERRORLEVEL% NEQ 0 (
    echo %RED%ERROR: Failed to start the database container.%RESET%
    echo       Make sure Docker Desktop is running.
    pause & exit /b 1
)

echo %YELLOW%[2/4] Waiting for database to be ready...%RESET%
timeout /t 5 /nobreak >nul

echo %YELLOW%[3/4] Running crawls...%RESET%
echo.

echo %CYAN%  → Amazon spider%RESET%
docker-compose run --rm scraper scrapy crawl amazon_spider
if %ERRORLEVEL% NEQ 0 (
    echo %RED%  WARNING: Amazon crawl exited with errors. Check logs above.%RESET%
)

echo.
echo %CYAN%  → Walmart spider%RESET%
docker-compose run --rm scraper scrapy crawl walmart_spider
if %ERRORLEVEL% NEQ 0 (
    echo %RED%  WARNING: Walmart crawl exited with errors. Check logs above.%RESET%
)

goto :query


:: =============================================================================
::  CRAWL: Amazon only
:: =============================================================================

:crawl_amazon
echo %YELLOW%Starting stack...%RESET%
docker-compose up -d db
timeout /t 5 /nobreak >nul
echo %CYAN%Running Amazon spider...%RESET%
docker-compose run --rm scraper scrapy crawl amazon_spider
goto :query


:: =============================================================================
::  CRAWL: Walmart only
:: =============================================================================

:crawl_walmart
echo %YELLOW%Starting stack...%RESET%
docker-compose up -d db
timeout /t 5 /nobreak >nul
echo %CYAN%Running Walmart spider...%RESET%
docker-compose run --rm scraper scrapy crawl walmart_spider
goto :query


:: =============================================================================
::  QUERY: display latest prices
:: =============================================================================

:query
echo.
echo %YELLOW%[4/4] Querying latest prices...%RESET%
echo.
docker-compose exec db psql -U %DB_USER% -d %DB_NAME% -c "%QUERY%"
if %ERRORLEVEL% NEQ 0 (
    echo %RED%ERROR: Could not connect to the database.%RESET%
    echo       Run "docker-compose up -d db" first.
    pause & exit /b 1
)
echo.
echo %GREEN%Done. Data is up to date.%RESET%
echo.
pause
exit /b 0


:: =============================================================================
::  RESET: wipe all data
:: =============================================================================

:reset
echo.
echo %RED%WARNING: This will permanently delete all scraped data.%RESET%
set /p CONFIRM=Type YES to confirm: 
if /i "!CONFIRM!" NEQ "YES" (
    echo Cancelled.
    pause & exit /b 0
)
echo %YELLOW%Stopping stack and wiping volumes...%RESET%
docker-compose down -v
echo %GREEN%Reset complete. Run sentinel.bat to start fresh.%RESET%
echo.
pause
exit /b 0