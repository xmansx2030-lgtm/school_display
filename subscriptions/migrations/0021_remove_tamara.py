from django.db import migrations, models


class Migration(migrations.Migration):
    """Tamara is discontinued: drop its checkout table and its payment method.

    The table held no rows, so nothing financial is lost. ``bank_transfer`` and
    ``moyasar`` are now the only two routes that can produce an invoice, and
    ``payment_link`` goes with them — it was a choice in a dropdown that never
    had any code behind it.
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
