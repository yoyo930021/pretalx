# Generated for OTP two-factor authentication support

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('person', '0032_speakerprofile_internal_notes'),
    ]

    operations = [
        migrations.CreateModel(
            name='OTPDevice',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ('secret', models.CharField(
                    help_text='Base32-encoded secret key for TOTP generation',
                    max_length=32,
                    verbose_name='Secret Key'
                )),
                ('enabled', models.BooleanField(
                    default=False,
                    help_text='Whether two-factor authentication is active for this user',
                    verbose_name='Enabled'
                )),
                ('created', models.DateTimeField(
                    auto_now_add=True,
                    verbose_name='Created'
                )),
                ('last_used', models.DateTimeField(
                    blank=True,
                    null=True,
                    verbose_name='Last Used'
                )),
                ('backup_codes', models.JSONField(
                    default=list,
                    help_text='One-time backup codes for account recovery',
                    verbose_name='Backup Codes'
                )),
                ('user', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='otp_device',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='User'
                )),
            ],
            options={
                'verbose_name': 'OTP Device',
                'verbose_name_plural': 'OTP Devices',
            },
        ),
    ]
