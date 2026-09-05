POLICY = {
    "get_business_details": [
        {"requires": "get_customer", "on": [("customer_id", "customer_id")],
         "condition": lambda result: result.get("acc_type") == "business"}
    ],
    "get_customer": [],  # no prerequisites
}