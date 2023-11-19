
function startAGGrid(data) {
    header = data.shift()
    rows = data.map((row) => {
        out = {}
        i=0
        for (i in header) {
            key = header[i]
            val = row[i]
            if (val == 'Default Title') {val = ''}
            if (['grams', 'price'].includes(key)) {
                val = Number(val)
            }
            if (['available'].includes(key)) {
                val = (val && val.toLowerCase() == 'true')
            }
            if (['updated_at', 'published_at'].includes(key)) {
                val = new Date(val)
            }
            out[key] = val
            i++
        }
        return out
    })
    rows.push({
        function: 'pricePerKilo',
        value: '=1000 * data.price / data.grams'
    })

    coldefs = header.map((col) => {
        out = {
            'headerName': col,
            'field': col,
            'filter': true
        }
        switch (col ){
            case '':
                out['hide'] = true
                break
            case 'product_url':
                out['cellRenderer'] = (params) => `<a href="${params.value}" target="_blank">${params.value}</a>`
                break
            }
        return out
        }
    )

    coldefs = [
        {
            'headerName': 'Vendor',
            'field': 'vendor'
        },
        {
            'headerName': 'Product Type',
            'field': 'product_type'
        },
        {
            'headerName': 'Title',
            'field': 'title'
        },
        {
            'headerName': 'Details',
            'field': 'item_title'
        },
        {
            'headerName': 'Grams',
            'field': 'grams',
            'cellDataType': 'number',
            'valueFormatter': params => `${params.data.grams}g`
        },
        {
            'headerName': 'Price',
            'field': 'price',
            'cellDataType': 'number',
            'valueFormatter': params => `\$${params.data.price.toFixed(2)}`
        },
        {
            'headerName': 'Price per Kilo',
            'field': 'pricePerKilo',
            'valueGetter': '1000 * data.price / data.grams',
            'valueFormatter': params => `\$${(1000 * params.data.price / params.data.grams).toFixed(2)}/kg`,
            'cellDataType': 'number'
        },
        {
            'headerName': 'Tags',
            'field': 'tags'
        },
        {
            'headerName': 'Available',
            'field': 'available',
            'cellDataType': 'boolean',
            'defaultOption': true
        },
        {
            'headerName': 'Published',
            'field': 'published_at',
            'cellDataType': 'date'
        },
        {
            'headerName': 'Updated',
            'field': 'updated_at',
            'cellDataType': 'date'
        },
        {
            'headerName': 'Link',
            'field': 'product_url',
            'cellRenderer': (params) => `<a href="${params.value}" target="_blank">${params.value}</a>`
        },
    ]

    // console.log(rows)
    // console.log(coldefs)
    const gridOptions = {
        columnDefs: coldefs,
        defaultColDef: {
            flex: 1,
            minWidth: 180,
            filter: true,
            floatingFilter: true,
            sortable: true,
            resizable: true,
            editable: true,
        },
        rowData: rows,
    }
    
    const eGridDiv = document.querySelector('#coffeedeals-grid')
    console.log('Grid Options:')
    console.log(gridOptions)
    new agGrid.Grid(eGridDiv, gridOptions)
}

// Kick off the download of CSV, transform to data arrays, and start AG Grid.
Papa.parse(
    './coffeedeals.csv', {
        download: true,
        complete: function(results){
            startAGGrid(results.data)
        }
    }
)