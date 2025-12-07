from django.dispatch import Signal, receiver

from pretalx.common.signals import login_form, register_data_exporters


@receiver(register_data_exporters, dispatch_uid="exporter_builtin_csv_speaker")
def register_speaker_csv_exporter(sender, **kwargs):
    from pretalx.person.exporters import CSVSpeakerExporter

    return CSVSpeakerExporter


delete_user = Signal()
"""
This signal is sent out when a user is deleted - both when deleted on the
frontend ("deactivated") and actually removed ("shredded").

You will get the user as a keyword argument ``user``. Receivers are expected to
delete any personal information they might have stored about this user.

Additionally, you will get the keyword argument ``db_delete`` when the user
object will be deleted from the database. If you have any foreign keys to the
user object, you should delete them here.
"""


@receiver(login_form, dispatch_uid="person_otp_login_form")
def add_otp_login_field(sender, request, **kwargs):
    """Add OTP verification field to login form if the user has 2FA enabled."""
    from pretalx.person.forms.otp import OTPLoginForm
    from pretalx.person.models import OTPDevice, User

    email = request.POST.get('login_email') or request.POST.get('register_email')
    if not email:
        return None

    try:
        user = User.objects.get(email__iexact=email)
        otp_device = user.otp_device
        if otp_device.enabled:
            return OTPLoginForm(user=user, **kwargs)
    except (User.DoesNotExist, OTPDevice.DoesNotExist):
        pass

    return None


@receiver(delete_user, dispatch_uid="person_delete_otp_device")
def delete_otp_device(sender, user, **kwargs):
    """Clean up OTP device when user is deleted."""
    from pretalx.person.models import OTPDevice

    try:
        if hasattr(user, 'otp_device'):
            user.otp_device.delete()
    except OTPDevice.DoesNotExist:
        pass
