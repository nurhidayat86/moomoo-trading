from datetime import date

from collector.partition import partition_date


def test_us_eastern_date_boundary():
    # 2024-01-02 02:30 UTC = 2024-01-01 21:30 ET
    part = partition_date("US.AAPL", "2024-01-01 21:30:00")
    assert part == date(2024, 1, 1)


def test_hk_shanghai_date():
    part = partition_date("HK.00700", "2024-06-15 10:00:00")
    assert part == date(2024, 6, 15)
