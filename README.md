# Senna Foods: SAP delivery file checker

Bonus script for the Logistics Flow Implementation Engineer Intern assignment.

It reads `SAP_delivery_sample.csv`, groups the item rows into deliveries, and prints each delivery's shop, number of items and total weight. Problem rows are flagged instead of crashing the run:

- missing weight (BRGEW blank)
- weight unit other than KG
- missing quantity
- unknown shop (the LMS knows every shop in the file except `0000100999`)

## Run

Python 3, no extra libraries.

```bash
python3 check_deliveries.py SAP_delivery_sample.csv
```

## Output

See [`output.txt`](output.txt): 12 deliveries, 21 item rows, 10 OK, 2 flagged (`0080001236` has no weight, and `0080001237` is for unknown shop `100999`).

## Notes

- Every column is read as text so leading zeros survive. VBELN is padded back to 10 digits in case a spreadsheet stripped them.
- Shop codes are compared the LMS way, without leading zeros (`0000100999` → `100999`).

## Mini-LMS

The design from the assignment, running as code with 13 automated tests: see [`mini_lms/`](mini_lms/README.md).
