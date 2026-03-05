if __name__ == "__main__":
    import json

    from iri_client import Client

    client = Client(base_url="https://api.iri.nersc.gov")

    # List operation ids
    operations = Client.operations()
    print(f"Loaded {len(operations)} operations from generated catalog")
    print("First 10 operations:")
    for operation in operations[:10]:
        print(f"  - {operation.operation_id} ({operation.method} {operation.path_template})")

    # Public operation
    print(client.call_operation("getFacility"))

    # Path params
    print(
        client.call_operation(
            "getSite",
            path_params_json=json.dumps({"site_id": "dd7f822a-3ad2-54ae-bddb-796ee07bd206"}),
        )
    )

    # Auth-required operation
    # access_token = "<token from GlobusAuth>"
    # auth_client = Client(base_url="https://api.iri.nersc.gov", access_token=access_token)
    # print(auth_client.call_operation("getProjects"))
