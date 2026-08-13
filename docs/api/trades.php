<?php
/**
 * 수익 관리(매매기록) CRUD.
 * GET    -> 전체 목록 + 계산된 수익률/수익금/진행기간
 * POST   -> 등록(id 없음) 또는 수정(id 있음)
 * DELETE -> 삭제 (JSON body에 {"id": ...})
 */

require_once __DIR__ . '/db.php';
require_once __DIR__ . '/auth.php';

require_auth();
header('Content-Type: application/json; charset=utf-8');

$pdo = get_db();
$method = $_SERVER['REQUEST_METHOD'];
$input = json_decode(file_get_contents('php://input'), true) ?? [];

function compute_fields(array $row): array
{
    $row['id'] = (int)$row['id'];
    $row['quantity'] = (int)$row['quantity'];
    $row['buy_price'] = (float)$row['buy_price'];
    $row['sell_price'] = $row['sell_price'] !== null ? (float)$row['sell_price'] : null;

    $has_sell = $row['sell_price'] !== null && !empty($row['sell_date']);

    if ($has_sell) {
        $row['status'] = 'closed';
        $row['profit_amount'] = round(($row['sell_price'] - $row['buy_price']) * $row['quantity'], 2);
        $row['profit_rate'] = $row['buy_price'] > 0
            ? round(($row['sell_price'] - $row['buy_price']) / $row['buy_price'] * 100, 2)
            : null;
        $buy = new DateTime($row['buy_date']);
        $sell = new DateTime($row['sell_date']);
        $row['holding_days'] = (int)$buy->diff($sell)->format('%a');
    } else {
        $row['status'] = 'open';
        $row['profit_amount'] = null;
        $row['profit_rate'] = null;
        $buy = new DateTime($row['buy_date']);
        $today = new DateTime('today');
        $row['holding_days'] = (int)$buy->diff($today)->format('%a');
    }

    return $row;
}

if ($method === 'GET') {
    $stmt = $pdo->query('SELECT * FROM trades ORDER BY buy_date DESC, id DESC');
    $rows = array_map('compute_fields', $stmt->fetchAll());
    echo json_encode(['trades' => $rows], JSON_UNESCAPED_UNICODE);
    exit;
}

if ($method === 'POST') {
    $corp_name = trim((string)($input['corp_name'] ?? ''));
    $stock_code = trim((string)($input['stock_code'] ?? ''));
    $quantity = (int)($input['quantity'] ?? 0);
    $buy_price = (float)($input['buy_price'] ?? 0);
    $buy_date = (string)($input['buy_date'] ?? '');
    $sell_price = (isset($input['sell_price']) && $input['sell_price'] !== '')
        ? (float)$input['sell_price'] : null;
    $sell_date = (isset($input['sell_date']) && $input['sell_date'] !== '')
        ? (string)$input['sell_date'] : null;

    if ($corp_name === '' || $quantity <= 0 || $buy_price <= 0 || $buy_date === '') {
        http_response_code(400);
        echo json_encode(['error' => '종목명/수량/매수가/매수일은 필수입니다.'], JSON_UNESCAPED_UNICODE);
        exit;
    }

    $id = (int)($input['id'] ?? 0);
    if ($id > 0) {
        $stmt = $pdo->prepare(
            'UPDATE trades SET corp_name=?, stock_code=?, quantity=?, buy_price=?, buy_date=?, sell_price=?, sell_date=? WHERE id=?'
        );
        $stmt->execute([$corp_name, $stock_code ?: null, $quantity, $buy_price, $buy_date, $sell_price, $sell_date, $id]);
    } else {
        $stmt = $pdo->prepare(
            'INSERT INTO trades (corp_name, stock_code, quantity, buy_price, buy_date, sell_price, sell_date) VALUES (?,?,?,?,?,?,?)'
        );
        $stmt->execute([$corp_name, $stock_code ?: null, $quantity, $buy_price, $buy_date, $sell_price, $sell_date]);
        $id = (int)$pdo->lastInsertId();
    }
    echo json_encode(['ok' => true, 'id' => $id], JSON_UNESCAPED_UNICODE);
    exit;
}

if ($method === 'DELETE') {
    $id = (int)($input['id'] ?? 0);
    if ($id <= 0) {
        http_response_code(400);
        echo json_encode(['error' => 'id가 필요합니다.'], JSON_UNESCAPED_UNICODE);
        exit;
    }
    $stmt = $pdo->prepare('DELETE FROM trades WHERE id=?');
    $stmt->execute([$id]);
    echo json_encode(['ok' => true], JSON_UNESCAPED_UNICODE);
    exit;
}

http_response_code(405);
echo json_encode(['error' => '지원하지 않는 메서드입니다.'], JSON_UNESCAPED_UNICODE);
