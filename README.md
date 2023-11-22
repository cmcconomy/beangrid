# Canadian Coffee Details

A little side project for me - I wanted to be able to search across various vendors' beans and find what I want to buy.

The interface is accessible here: https://cmcconomy.github.io/coffeedeals  
The CSV can be directly accessed via https://cmcconomy.github.io/coffeedeals/coffeedeals.csv

## Data processing
The approach is fairly simple: 
- parse Shopify product published on various vendors sites
- assemble into a single CSV file

A new run is launched via github action on a cron schedule, which replaces the CSV content.  
A new push to main will also publish the most recent contents of [`./docs`](docs) to the github pages site above.

## Presentation
- The main landing page uses AG Grid for data querying and exploration
- For the nerdier folks, a [link on the page](https://lite.datasette.io/?csv=https%3A%2F%2Fcmcconomy.github.io%2Fcoffeedeals%2Fcoffeedeals.csv#/data/coffeedeals) loads the data into datasette-lite. Datasette reads the CSV and converts on the fly to a sqlite database, which datasette then provides access for
- If that's not enough, I also added a Jupyter Lite environment where you can delve into the data.

# Contributing
You can send in pull requests if there are URLS for other shopify-based canadian coffee roasters you would like me to add to the list.

Appreciate this project? [Buy me a coffee!](https://www.buymeacoffee.com/Cmcconomy)