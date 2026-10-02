# 2026-09-25 (kullanıcı kararı):
#  • DosyaTaraf.tebligat_durum_tarihi — borçlu başına ödeme emri tebliğ/iade
#    tarihi (tebligat_durumu ile birlikte dosya_core.kesinlesme_durumlarini_guncelle yazar).
#  • Dosya.yargi_turu ZORUNLU — "icra dosyası mı" tek alandan cevaplanır.
#    Uygulama anında 44.649 dosyanın hiçbirinde boş değildi; yine de boş kalan
#    olursa birimin yargı türünden doldurulur (0020 ile aynı kural, uydurma yok).
#    Birimi de bilinmiyorsa AlterField hata verir — sessizce tür atanmaz.

from django.db import migrations, models


def bos_yargi_turunu_birimden_doldur(apps, schema_editor):
    Dosya = apps.get_model("icra_models", "Dosya")
    for d in Dosya.objects.filter(yargi_turu__isnull=True).select_related("birim"):
        if d.birim.yargi_turu is not None:
            Dosya.objects.filter(pk=d.pk).update(yargi_turu=d.birim.yargi_turu)


class Migration(migrations.Migration):

    dependencies = [
        ('icra_models', '0024_icratakipdetay_bsmv_ucreti_icratakipdetay_kdv_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='dosyataraf',
            name='tebligat_durum_tarihi',
            field=models.DateField(blank=True, null=True, verbose_name='Tebliğ Durumu Tarihi'),
        ),
        migrations.RunPython(bos_yargi_turunu_birimden_doldur, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='dosya',
            name='yargi_turu',
            field=models.PositiveSmallIntegerField(choices=[(0, 'Ceza'), (1, 'Hukuk'), (2, 'İcra'), (3, 'Cbs'), (6, 'İdari Yargı'), (11, 'Satış Memurluğu'), (25, 'Arabuluculuk'), (26, 'Tazminat Komisyonu Başkanlığı')], db_index=True, verbose_name='Yargı Türü Kodu'),
        ),
    ]
