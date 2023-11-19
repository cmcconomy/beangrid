import json
from importlib import resources as impresources
from bs4 import BeautifulSoup

import httpx
import pandas as pd

import coffeedeals


def scrape_shopify(base_url: str) -> pd.DataFrame:
    url = base_url + '/products.json'

    def get_page(page):
        data = httpx.get(url + '?page={}'.format(page)).text
        products = json.loads(data)['products']
        return products
    
    def rename_variant_dupe_cols(vdf: pd.DataFrame, prdf: pd.DataFrame):
        dupe_cols = set(vdf.columns).intersection(set(prdf.columns))
        rename_spec = {col:f"item_{col}" for col in dupe_cols}
        vdf.rename(columns=rename_spec, inplace=True)
    
    dfs = []
    page = 1
    products = get_page(page)
    while products:
        for product in products:
            variants = product['variants']
            del product['variants']

            vdf = pd.json_normalize(variants)
            prdf = pd.json_normalize(product)
            prdf['product_url'] = base_url + '/products/' + product['handle']
            prdf['description'] = BeautifulSoup(prdf['body_html'], 'html.parser').text
            del prdf['body_html']
            rename_variant_dupe_cols(vdf, prdf)
            dfs.append(vdf.merge(prdf, how='cross'))

        page += 1
        products = get_page(page)
    return pd.concat(dfs).reset_index().drop(columns='index').dropna(how='all')

if __name__ == '__main__':
    inp_file = (impresources.files(coffeedeals) / 'shopify_sites.txt')
    with open(inp_file, 'r') as f:
        sites = f.read().split('\n')

    dfs = []
    for site in sites:
        try:
            dfs.append(scrape_shopify(site))
        except Exception as e:
            raise Exception("Error processing site %s" % site) from e

    df = pd.concat(dfs)
    print(df.to_csv())