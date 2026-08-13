<?php
/**
 * 수익 관리(trades) 테이블용 공용 DB 연결 헬퍼.
 * 첫 연결 시 테이블이 없으면 만들어서, 사용자가 닷홈 DB 관리 화면에서 따로
 * SQL을 실행할 필요가 없게 한다.
 */

require_once __DIR__ . '/config.php';

function get_db(): PDO
{
    static $pdo = null;
    if ($pdo === null) {
        $dsn = 'mysql:host=' . DB_HOST . ';dbname=' . DB_NAME . ';charset=utf8mb4';
        $pdo = new PDO($dsn, DB_USER, DB_PASSWORD, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
        $pdo->exec(
            'CREATE TABLE IF NOT EXISTS trades (
                id INT AUTO_INCREMENT PRIMARY KEY,
                corp_name VARCHAR(100) NOT NULL,
                stock_code VARCHAR(20) DEFAULT NULL,
                quantity INT NOT NULL,
                buy_price DECIMAL(15,2) NOT NULL,
                buy_date DATE NOT NULL,
                sell_price DECIMAL(15,2) DEFAULT NULL,
                sell_date DATE DEFAULT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'
        );
    }
    return $pdo;
}
