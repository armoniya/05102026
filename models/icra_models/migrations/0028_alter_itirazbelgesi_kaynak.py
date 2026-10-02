# 2026-09-30 (kullanıcı kuralı): ItirazBelgesi.Kaynak'a "UYAP Dosya Durumu
# (Takibe İtiraz)" seçeneği — yalnız choices değişir, veritabanı şeması aynı.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('icra_models', '0027_dosyataraf_kesinlesme_tarihi'),
    ]

    operations = [
        migrations.AlterField(
            model_name='itirazbelgesi',
            name='kaynak',
            field=models.CharField(choices=[('evrak_listesi', 'UYAP Evrak Listesi'), ('tebligat_zarfi', 'Tebligat Zarfı İçeriği'), ('dosya_durumu', 'UYAP Dosya Durumu (Takibe İtiraz)')], default='evrak_listesi', max_length=20),
        ),
    ]
