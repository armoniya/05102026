# 2026-09-30 (kullanıcı isteği): DosyaTaraf.kesinlesme_tarihi — borçlu başına
# kesinleşme günü (dosya_core.kesinlesme_durumlarini_guncelle yazar).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('icra_models', '0026_alter_dosyataraf_kesinlesme_durumu_itirazbelgesi'),
    ]

    operations = [
        migrations.AddField(
            model_name='dosyataraf',
            name='kesinlesme_tarihi',
            field=models.DateField(blank=True, null=True, verbose_name='Kesinleşme Tarihi'),
        ),
    ]
