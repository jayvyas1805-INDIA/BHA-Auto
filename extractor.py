def extract_to_json(data):

    rows=[]


    for table in data["tables"]:

        for row in table:

            rows.append(
                row
            )


    return {

        "pages":
        data["pages"],

        "full_content":
        data["text"],

        "tables":
        rows

    }