from django.contrib.auth.decorators import user_passes_test

clergy_required = user_passes_test(lambda u: u.is_authenticated and not u.is_staff, login_url='index')
