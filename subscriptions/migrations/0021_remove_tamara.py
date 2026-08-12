from django.db import migrations, models


class Migration(migrations.Migration):
    """Tamara is discontinued: drop its checkout table and its payment method.

    Production holds three rows, and all three are dead checkout attempts: one
    ``error`` that failed validation before Tamara was ever called, and two
    ``expired`` sessions that timed out unpaid. None reached ``authorised`` or
    ``captured`` and none carries a ``payment_operation``, so no invoice, no
    payment operation and no money references this table — nothing financial is
    lost by dropping it. (The deploy dumps the database before migrating, so the
    rows remain readable in that dump if anyone ever wants the audit trail.)

    ``bank_transfer`` and ``moyasar`` are now the only two routes that can
    produce an invoice, and ``payment_link`` goes with them — it was a choice in
    a dropdown that never had any code behind it.
    """

    dependencies = [
        ("subscriptions", "0020_moyasarcheckout_extra_screens_and_more"),
    ]

    operations = [
        # The index has to go before the model: SQLite rebuilds a table to drop
        # columns, and it re-creates the declared indexes while doing so — which
        # fails once the columns they name are on their way out.
        migrations.RemoveIndex(
            model_name="tamaracheckout",
            name="tamara_school_status_idx",
        ),
        migrations.DeleteModel(name="TamaraCheckout"),
        migrations.AlterField(
            model_name="subscriptionpaymentoperation",
            name="method",
            field=models.CharField(
                choices=[("bank_transfer", "تحويل بنكي"), ("moyasar", "ميسر")],
                max_length=20,
                verbose_name="طريقة الدفع",
            ),
        ),
        migrations.AlterField(
            model_name="subscriptioninvoice",
            name="payment_method",
            field=models.CharField(
                choices=[("bank_transfer", "تحويل بنكي"), ("moyasar", "ميسر")],
                max_length=20,
                verbose_name="طريقة الدفع",
            ),
        ),
    ]
