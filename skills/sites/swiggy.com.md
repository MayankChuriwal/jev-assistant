---
host: swiggy.com
name: Swiggy
aliases: Instamart, Swiggy Instamart
start: https://www.swiggy.com
start.groceries: https://www.swiggy.com/instamart
search: https://www.swiggy.com/instamart/search?query={query}
---
- Instamart (groceries) lives under https://www.swiggy.com/instamart; restaurant food is the main site.
- The main site's search box and its results are restaurants only: never use them for groceries.
- The delivery location sits at the top left; set it before searching.
- If /instamart sends you to the home page, no location is set: set it there first, then OPEN_URL https://www.swiggy.com/instamart.
- For groceries, OPEN_URL the search address above (Instamart's) once the location is set; for restaurants use the site's search box.
- Icon-only tiles may carry short labels such as "IM" for Instamart.
- Some accounts are offered only saved cards for restaurant orders (no cash on delivery).
