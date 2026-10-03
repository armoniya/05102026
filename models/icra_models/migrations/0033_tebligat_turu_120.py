from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('icra_models', '0032_dosya_islem_durumu'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tebligatbarkod',
            name='tebligat_turu',
            field=models.CharField(blank=True, max_length=120, verbose_name='Tebligat Türü'),
        ),
    ]
