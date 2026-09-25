POLICY = {
    "get_business_details": [
        {"requires": "get_customer", "on": [("customer_id", "customer_id")],
         "condition": lambda result: result.get("acc_type") == "business"}
    ],
    "get_customer": [],  # no prerequisites
    
     # Exercise 5 entries
    "get_coordinates_for_city": [],   # no prerequisites

    "get_weather": [
        {
            "requires": "get_coordinates_for_city",
            "on": [("latitude", "latitude"), ("longitude", "longitude")],
            "condition": None,
            "allow_user_supplied": True,        # new
            "match_fields": ["latitude", "longitude"]  # new — what to look for in prompt
        }
    ]
}