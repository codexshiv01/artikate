"""
Management command to seed the database with realistic order data
for profiling the N+1 query problem.

Usage:
    python manage.py seed_orders
    python manage.py seed_orders --orders 500 --items-per-order 5
"""
import random
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from section1_orders.models import Product, Order, OrderItem


class Command(BaseCommand):
    help = 'Seed the database with orders for N+1 query profiling'

    def add_arguments(self, parser):
        parser.add_argument(
            '--orders', type=int, default=300,
            help='Number of orders to create (default: 300)',
        )
        parser.add_argument(
            '--items-per-order', type=int, default=3,
            help='Number of items per order (default: 3)',
        )

    def handle(self, *args, **options):
        num_orders = options['orders']
        items_per_order = options['items_per_order']

        # Create or get user
        user, created = User.objects.get_or_create(
            username='demo_user',
            defaults={
                'email': 'demo@artikate.com',
                'first_name': 'Demo',
                'last_name': 'User',
            },
        )
        if created:
            user.set_password('demo123')
            user.save()
            self.stdout.write(f'Created user: {user.username}')
        else:
            self.stdout.write(f'Using existing user: {user.username}')

        # Create products
        product_names = [
            ('Wireless Mouse', 'MOUSE'),
            ('Mechanical Keyboard', 'KEYBD'),
            ('USB-C Hub', 'USBC'),
            ('Monitor Stand', 'MNSTR'),
            ('Webcam HD', 'WEBCM'),
            ('Desk Lamp', 'LAMP'),
            ('Headphones', 'HEADP'),
            ('Mouse Pad XL', 'MPAD'),
            ('Cable Organizer', 'CABLE'),
            ('Laptop Stand', 'LSTND'),
            ('Screen Protector', 'SCRPR'),
            ('Phone Mount', 'PHMNT'),
            ('Power Strip', 'PWRST'),
            ('Desk Mat', 'DSKMT'),
            ('USB Drive 64GB', 'USB64'),
        ]

        products = []
        for name, sku_prefix in product_names:
            product, _ = Product.objects.get_or_create(
                sku=f'{sku_prefix}-001',
                defaults={
                    'name': name,
                    'price': round(random.uniform(9.99, 199.99), 2),
                    'category': random.choice(['electronics', 'accessories', 'office']),
                },
            )
            products.append(product)

        self.stdout.write(f'Products ready: {len(products)}')

        # Create orders in bulk
        statuses = ['pending', 'confirmed', 'shipped', 'delivered']

        existing_count = Order.objects.filter(user=user).count()
        if existing_count >= num_orders:
            self.stdout.write(
                self.style.WARNING(
                    f'User already has {existing_count} orders. Skipping seed.'
                )
            )
            return

        orders_to_create = []
        for _ in range(num_orders):
            orders_to_create.append(
                Order(
                    user=user,
                    status=random.choice(statuses),
                    total_amount=0,  # Will update after items
                )
            )

        Order.objects.bulk_create(orders_to_create, batch_size=500)
        created_orders = Order.objects.filter(user=user).order_by('-id')[:num_orders]

        self.stdout.write(f'Created {num_orders} orders')

        # Create order items in bulk
        items_to_create = []
        for order in created_orders:
            selected_products = random.sample(products, min(items_per_order, len(products)))
            total = 0
            for product in selected_products:
                qty = random.randint(1, 5)
                items_to_create.append(
                    OrderItem(
                        order=order,
                        product=product,
                        quantity=qty,
                        unit_price=product.price,
                    )
                )
                total += qty * product.price

            order.total_amount = round(total, 2)

        OrderItem.objects.bulk_create(items_to_create, batch_size=1000)

        # Bulk update totals
        Order.objects.bulk_update(
            list(created_orders), ['total_amount'], batch_size=500
        )

        total_items = len(items_to_create)
        self.stdout.write(
            self.style.SUCCESS(
                f'\nSeed complete!'
                f'\n  User: {user.username}'
                f'\n  Orders: {num_orders}'
                f'\n  Items: {total_items}'
                f'\n  Products: {len(products)}'
                f'\n\nNow profile the endpoints:'
                f'\n  Broken: GET /api/orders/summary/'
                f'\n  Fixed:  GET /api/orders/summary/fixed/'
                f'\n  Silk:   GET /silk/'
            )
        )
