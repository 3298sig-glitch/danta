<?php
/**
 * 실제 값은 절대 이 파일이나 git에 커밋하지 않는다.
 *
 * 배포는 .github/workflows/deploy_profit_backend.yml이 담당한다 - GitHub Secrets
 * (DB_HOST/DB_NAME/DB_USER/DB_PASSWORD/PROFIT_PIN)에서 값을 읽어 배포 시점에
 * docs/api/config.php를 새로 만들어 올리고, 그 파일 자체는 커밋하지 않는다
 * (.gitignore에 등록돼 있음).
 *
 * 로컬에서 테스트하려면 이 파일을 config.php로 복사하고 실제 값을 채워 넣을 것.
 */
define('DB_HOST', 'localhost');
define('DB_NAME', 'sigg3298');
define('DB_USER', 'sigg3298');
define('DB_PASSWORD', 'CHANGE_ME');
define('PROFIT_PIN', '0000');
