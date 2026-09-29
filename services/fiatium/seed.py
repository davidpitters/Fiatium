from sqlalchemy import text

from fiatium.db import engine, lock, one
from fiatium.domain import InvoiceInput
from fiatium.payments import create_invoice


def main():
    customer_id = "11111111-1111-4111-8111-111111111111"
    with engine().begin() as conn:
        lock(conn, "seed:demo")
        if not one(conn, "SELECT id FROM customers WHERE id=:id", id=customer_id):
            conn.execute(
                text("INSERT INTO customers(id,tenant,name) VALUES(:id,'demo',:name)"),
                {"id": customer_id, "name": "Maple Studio (simulated)"},
            )
    with engine().connect() as conn:
        exists = one(conn, "SELECT TOP (1) id FROM invoices WHERE tenant='demo'")
    if not exists:
        create_invoice("demo", InvoiceInput(customer_id=customer_id, amount=12500))
    print("Demo customer and invoice ready")


if __name__ == "__main__":
    main()
