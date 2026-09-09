from desktop_app.gpu import classify_gpu, select_gpu


def item(cc='8.9', total=16376, free=12000, driver='595.79', uuid='one'):
    name = {'7.5': 'NVIDIA GeForce RTX 2070 SUPER', '8.6': 'NVIDIA GeForce RTX 3070', '8.9': 'NVIDIA GeForce RTX 4070 Ti SUPER', '12.0': 'NVIDIA GeForce RTX 5070'}.get(cc, 'Unknown')
    return classify_gpu(dict(index=0, uuid=uuid, name=name, compute_capability=cc, total_mb=total, free_mb=free, driver=driver))


def test_runtime_selection_and_reserved_vram():
    assert item('7.5', total=7982)['status'] == 'compatible'
    assert item('8.6')['runtime_id'] == 'cu121'
    assert item('12.0')['runtime_id'] == 'cu128'
    assert item('12.0', driver='560.00')['status'] == 'driver_update_required'
    assert item('12.0', driver='560.00', free=2000)['status'] == 'driver_update_required'
    assert item('6.1')['status'] == 'unsupported_architecture'
    assert item(total=6144)['status'] == 'insufficient_vram'


def test_choose_most_free_eligible_board():
    chosen = select_gpu([item(free=9000), item(free=11000, uuid='two'), item(free=3000, uuid='busy')])
    assert chosen['uuid'] == 'two'
    assert select_gpu([item(free=9000), item(free=11000, uuid='two')], 'one')['uuid'] == 'one'
    assert select_gpu([item(free=3000), item(free=11000, uuid='two')], 'one') is None


def test_family_and_architecture_must_agree():
    gpu = item()
    gpu['name'] = 'NVIDIA RTX A4000'
    assert classify_gpu(gpu)['status'] == 'unsupported_architecture'
    gpu['name'] = 'NVIDIA GeForce GTX 1660'
    gpu['compute_capability'] = '7.5'
    assert classify_gpu(gpu)['status'] == 'unsupported_architecture'
    gpu['name'] = 'NVIDIA GeForce RTX 5070'
    assert classify_gpu(gpu)['status'] == 'unsupported_architecture'
