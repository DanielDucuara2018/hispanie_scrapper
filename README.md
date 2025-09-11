# Facebook Event Scraper

This project is a web scraper designed to extract event details from Facebook event pages. It uses Playwright for browser automation and supports exporting event data to JSON files and sending event reports via email.

## Features

- Scrapes event details such as title, date, location, description, and banner image.
- Supports French date and time formats.
- Saves event data as a JSON file.
- Sends event reports via email with the JSON file attached.

## Requirements

- Python 3.12+
- Required Python libraries:
  - `playwright`
  - `smtplib`
  - `json`
  - `locale`

## Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd hispanie_scrapper
   ```
2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Install Playwright browsers:
   ```bash
   playwright install chromium
   ```

## Usage

1. Update the `keywords` list in the `__main__` section with the event keywords you want to search for.
2. Run the script:
   ```bash
   python facebook_event_scraper.py
   ```
3. The script will:
   - Scrape event details based on the keywords and city.
   - Save the event data as a JSON file in the `output` folder.
   - Send an email with the event report and JSON file attached.

## Configuration

- **Email Settings**: Update the `send_events_email` function with your email credentials and recipient email addresses.
- **Locale**: Ensure your system supports the `fr_FR.UTF-8` locale for French date formatting.

## Example Output

The JSON file will contain event details like:

```json
[
  {
    "title": "SALSA NIGHT",
    "date": "Vendredi - 20H à 23H",
    "location": "Paris, France",
    "description": "An amazing salsa night under the stars!",
    "image": "https://example.com/banner.jpg",
    "link": "https://facebook.com/events/123456789",
    "cost": "Free",
    "type": "Dance"
  }
]
```

## License

This project is licensed under the MIT License.
