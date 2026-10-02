-- EDNP Clergy Records System -- MySQL schema + seed data
-- Generated from the current in-browser data (admin/js/data.js records[],
-- shared/accounts.js defaultClergyAccount(), and admin/js/clergy-records.js
-- deaneries object) so the database starts in sync with the app's demo state.
--
-- Import: mysql -u root -p < ednp_schema.sql
-- or via phpMyAdmin: Import tab -> choose this file -> Go.
--
-- NOTE: passwords below are plaintext, matching the current prototype's
-- localStorage-based login. Before wiring real authentication on top of this
-- database, hash them properly (e.g. Django's make_password(), or PHP's
-- password_hash()) and update the login/verification code to match.

CREATE DATABASE IF NOT EXISTS ednp_clergy_records
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE ednp_clergy_records;

-- =========================================================
-- SCHEMA
-- =========================================================

CREATE TABLE deaneries (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL UNIQUE
) ENGINE=InnoDB;

CREATE TABLE parishes (
  id INT AUTO_INCREMENT PRIMARY KEY,
  deanery_id INT NOT NULL,
  parish_name VARCHAR(150) NOT NULL,
  place_name VARCHAR(150) NULL,
  FOREIGN KEY (deanery_id) REFERENCES deaneries(id) ON DELETE CASCADE
) ENGINE=InnoDB;

CREATE TABLE clergy_records (
  id VARCHAR(20) PRIMARY KEY,
  name VARCHAR(150) NOT NULL,
  gender ENUM('Male','Female') NULL,
  date_of_birth DATE NULL,
  baptism_date DATE NULL,
  confirmation_date DATE NULL,
  ordination_date DATE NULL,
  phone VARCHAR(30) NULL,
  email VARCHAR(150) NULL,
  address VARCHAR(255) NULL,
  assignment VARCHAR(150) NULL,
  status ENUM('Active','Assigned Abroad','Inactive','Retired') NOT NULL DEFAULT 'Active',
  contract_end DATE NULL,
  retirement_date DATE NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

CREATE TABLE clergy_accounts (
  username VARCHAR(50) PRIMARY KEY,
  password VARCHAR(255) NOT NULL,
  record_id VARCHAR(20) NULL UNIQUE,
  name VARCHAR(150) NULL,
  first_name VARCHAR(80) NULL,
  middle_name VARCHAR(80) NULL,
  last_name VARCHAR(80) NULL,
  suffix VARCHAR(20) NULL,
  birth_date DATE NULL,
  place_of_birth VARCHAR(150) NULL,
  gender ENUM('Male','Female') NULL,
  civil_status ENUM('Single','Married','Widowed') NULL,
  baptism_date DATE NULL,
  baptism_place VARCHAR(150) NULL,
  confirmation_date DATE NULL,
  confirmation_place VARCHAR(150) NULL,
  address VARCHAR(255) NULL,
  contact VARCHAR(30) NULL,
  email VARCHAR(150) NULL,
  ordination_date DATE NULL,
  ordination_place VARCHAR(150) NULL,
  assignment VARCHAR(150) NULL,
  previous_assignment VARCHAR(150) NULL,
  education TEXT NULL,
  other_information TEXT NULL,
  status ENUM('Active','Assigned Abroad','Inactive','Retired') NULL,
  diocese VARCHAR(150) NULL,
  contract_end DATE NULL,
  verification ENUM('Verified','For Verification') NOT NULL DEFAULT 'For Verification',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (record_id) REFERENCES clergy_records(id) ON DELETE SET NULL
) ENGINE=InnoDB;

CREATE TABLE admin_users (
  username VARCHAR(50) PRIMARY KEY,
  password VARCHAR(255) NOT NULL,
  full_name VARCHAR(150) NOT NULL,
  role VARCHAR(50) NOT NULL DEFAULT 'Registrar',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- =========================================================
-- SEED DATA -- deaneries and parishes
-- Source: 1st quarter report 2025 statistics.xlsx (one sheet per
-- deanery). "Special Assignments" is not from the spreadsheet -- it
-- covers non-parish postings (diocesan office duty, retirement, etc).
-- =========================================================

INSERT INTO deaneries (name) VALUES
  ('Central Deanery'),
  ('Titus Deanery'),
  ('Besao Deanery'),
  ('Bauko Deanery'),
  ('Kinali Deanery'),
  ('Sagada Deanery'),
  ('Tadian Deanery'),
  ('Special Assignments');

INSERT INTO parishes (deanery_id, parish_name, place_name) VALUES
  (1, 'St. Barnabas', 'Alab'),
  (1, 'St. Thomas', 'Balili'),
  (1, 'St. George', 'Bilig'),
  (1, 'All Saints Cathedral', 'Bontoc'),
  (1, 'Holy Trinity', 'Dalikan'),
  (1, 'St. Timothy\'s', 'Dantay'),
  (1, 'St. Augustine\'s', 'Gonogon'),
  (1, 'St. Michael\'s', 'Guina-ang'),
  (1, 'Annunciation', 'Maggon'),
  (1, 'St. Joseph\'s', 'Mainit'),
  (1, 'St. Gabriel\'s', 'Maligcong'),
  (1, 'Favuyan Mission', 'Maligcong'),
  (1, 'St. Peter\'s', 'Sabangan'),
  (1, 'St. Paul', 'Samoki'),
  (1, 'St. Matthias', 'Tambingan'),
  (1, 'St. Agnes', 'Payag-eo'),
  (1, 'Mission', 'Barlig'),
  (1, 'Shalom', 'Capinitan'),
  (1, 'Mission', 'Talubin'),
  (2, 'St. Theodore\'s', 'Basao'),
  (2, 'St. Andrews', 'Bugnay'),
  (2, 'St. Paul\'s', 'Belwang'),
  (2, 'St. Bernard', 'Buscalan'),
  (2, 'St. James', 'Loccong'),
  (2, 'Holy Cross', 'Tocucan'),
  (2, 'Dominic\'s', 'Tulgao'),
  (2, 'St. Andrew\'s', 'Bugnay'),
  (2, 'St. Luke\'s', 'Butbut'),
  (2, 'Transfiguration church', 'Maswa'),
  (2, 'St. Philip', 'Ngibat'),
  (2, 'Holy Trinity', 'Tinglayan'),
  (3, 'St. Augustine', 'Agawa'),
  (3, 'St. Hippolytus', 'Ambagiw'),
  (3, 'St. Marks', 'Banguitan'),
  (3, 'St. Anne', 'Besao'),
  (3, 'St. Philip', 'Gueday'),
  (3, 'St. Benidict', 'Kin-iway'),
  (3, 'St. John the Divine', 'Lacmaan'),
  (3, 'St. Basil', 'Masameyeo'),
  (3, 'St. Caroline', 'Padangaan'),
  (3, 'St. Clement', 'Payeo'),
  (3, 'St. Ambrose', 'Suquib'),
  (3, 'St. Luke\'s', 'Pangweo'),
  (3, 'St. Dunstan', 'Catenga'),
  (3, 'St. Jerome', 'Bunga'),
  (4, 'Holy Apostle', 'Abatan'),
  (4, 'St. Gregory', 'Bagnen'),
  (4, 'St. Andrew', 'Bagnen Oriente'),
  (4, 'Saint Martin', 'Balintaugan'),
  (4, 'Holy Innocent', 'Bebe'),
  (4, 'St. John', 'Bila'),
  (4, 'St. Gabriel', 'Data'),
  (4, 'San Luis', 'Mabaay'),
  (4, 'San Jose', 'Madepdepas'),
  (4, 'St. Matthias', 'Mayag'),
  (4, 'St. Mary', 'Mount Data'),
  (4, 'St. Paul', 'Otucan'),
  (4, 'St. Leo', 'Sadsadan'),
  (4, 'St. Francis', 'Cagubatan'),
  (4, 'St. Anselm', 'Pandayan'),
  (4, 'St. Bernard', 'Pangao'),
  (4, 'St. Mark', 'Sinto'),
  (4, 'Mission', 'Sengyew'),
  (4, 'San Pedro', 'Pasbol'),
  (4, 'St. John', 'Pasnadan'),
  (4, 'St. Gregory', 'Soysoyoc'),
  (4, 'St. Martin', 'Gotang'),
  (5, 'St Cyril', 'Bab-asig'),
  (5, 'St. Jerome', 'Bunga'),
  (5, 'St. Dunstan', 'Catengan'),
  (5, 'St. Hilarys', 'Dandanac'),
  (5, 'St. Marks', 'Pananuman'),
  (5, 'St. Bede', 'Panabungen'),
  (5, 'St. Andrew', 'Patiacan'),
  (5, 'St. Cyprian', 'Mabalite'),
  (5, 'St. Alfred', 'Tamboan'),
  (5, 'St. Luke', 'Maliten'),
  (5, 'St. Joseph', 'Lamag'),
  (5, 'St. Mathias', 'Supo'),
  (5, 'St. Stephen', 'Legleg'),
  (5, 'St. Polycarp', 'Matibuey'),
  (6, 'Ignacia & Juan', 'Aguid'),
  (6, 'St. Columba', 'Ambasing'),
  (6, 'Annunciation', 'Antadao'),
  (6, 'St. John', 'Balugan'),
  (6, 'St. Matthew', 'Bangaan'),
  (6, 'St. Mary Magdalene', 'Fedelisan'),
  (6, 'St. Elizabeth', 'Guesang'),
  (6, 'St. John', 'Pide'),
  (6, 'St. Mark', 'Tanulong'),
  (6, 'St. Mary', 'Tetep-an'),
  (6, 'St. Mary the Virgin', 'Sagada'),
  (6, 'Corpus Christi', 'Soyu'),
  (6, 'St. Simon Peter', 'Demang'),
  (6, 'St. Stephen', 'Nacagang'),
  (6, 'St. Agnes', 'Pide'),
  (6, 'St. aidan\'s', 'Ankileng'),
  (7, 'Holy Trinity', 'Bantey'),
  (7, 'Holy Innocent', 'Batayan'),
  (7, 'St. Martha', 'Cabunagan'),
  (7, 'St. Francis', 'New Lubon'),
  (7, 'St. Gabriel', 'Lubon'),
  (7, 'St. Joseph', 'Masla'),
  (7, 'St. John the Baptist', 'Ilang'),
  (7, 'Mission', 'Duagan'),
  (7, 'Epiphany', 'Sumadel'),
  (7, 'St. Michael & all angels', 'Tadian'),
  (7, 'St. Philip', 'Tue'),
  (7, 'St. Thomas', 'Balaoa'),
  (7, 'St. Ignatius', 'Kayan'),
  (7, 'St. Catherine', 'Cervantes'),
  (7, 'St. Mark', 'Nabitic'),
  (7, 'St. Stephen', 'Bunga'),
  (7, 'St. Cyprian', 'Mabalite'),
  (8, 'Diocesan Office', NULL),
  (8, 'Overseas Mission', NULL),
  (8, 'Retired Clergy', NULL),
  (8, 'Former Parish', NULL);

-- 118 parish rows across 8 deaneries

-- =========================================================
-- SEED DATA -- clergy records (from admin/js/data.js records[])
-- =========================================================

INSERT INTO clergy_records (id, name, gender, date_of_birth, baptism_date, confirmation_date, ordination_date, phone, email, address, assignment, status, contract_end, retirement_date) VALUES
  ('CLG-001', 'Rev. Fr. Juan Dela Cruz', 'Male', '1982-05-14', '1982-06-20', '1994-05-15', '2014-06-20', '0917-123-4567', 'juan.delacruz@example.org', 'Dagupan City, Pangasinan', 'St. Peter Parish', 'Active', '2027-03-31', NULL),
  ('CLG-002', 'Rev. Fr. Pedro Santos', 'Male', '1978-09-21', '1978-10-01', '1990-04-22', '2010-05-18', '0918-222-3344', 'pedro.santos@example.org', 'Dagupan City, Pangasinan', 'Diocesan Office', 'Active', '2027-01-15', NULL),
  ('CLG-003', 'Rev. Fr. Marco Reyes', 'Male', '1969-02-10', '1969-03-02', '1981-05-10', '1999-04-12', '0919-333-4455', 'marco.reyes@example.org', 'San Carlos City, Pangasinan', 'St. Joseph Parish', 'Active', '2026-09-10', NULL),
  ('CLG-004', 'Rev. Fr. Antonio Garcia', 'Male', '1960-11-02', '1960-12-04', '1972-05-21', '1988-07-01', '0920-444-5566', 'antonio.garcia@example.org', 'Urdaneta City, Pangasinan', 'Rome Mission', 'Assigned Abroad', '2026-12-20', NULL),
  ('CLG-005', 'Rev. Fr. Luis Mendoza', 'Male', '1957-03-16', '1957-04-07', '1969-05-18', '1984-05-11', '0921-555-6677', 'luis.mendoza@example.org', 'Pozorrubio, Pangasinan', 'Retired Clergy', 'Retired', NULL, '2019-12-31'),
  ('CLG-006', 'Rev. Fr. Ramon Cruz', 'Male', '1985-07-08', '1985-08-04', '1997-05-25', '2016-03-25', '0922-666-7788', 'ramon.cruz@example.org', 'Calasiao, Pangasinan', 'Former Parish', 'Inactive', NULL, NULL);

-- =========================================================
-- SEED DATA -- clergy portal account (from shared/accounts.js
-- defaultClergyAccount()). Linked to CLG-001 (Rev. Fr. Juan Dela Cruz)
-- since that is clearly the same person the standalone client demo
-- account described (matching name) -- the original prototype had
-- mismatched IDs (CLG-000001 vs CLG-001) since they were two separate
-- files before this database unified them.
-- =========================================================

INSERT INTO clergy_accounts (username, password, record_id, name, first_name, middle_name, last_name, suffix, birth_date, place_of_birth, gender, civil_status, baptism_date, baptism_place, confirmation_date, confirmation_place, address, contact, email, ordination_date, ordination_place, assignment, previous_assignment, education, other_information, status, diocese, contract_end, verification) VALUES
  ('clergy', 'password', 'CLG-001', 'Rev. Fr. Juan Dela Cruz', 'Juan', NULL, 'Dela Cruz', NULL, '1985-05-15', 'Baguio City', 'Male', 'Single', '1985-06-01', 'St. Peter Parish', '1990-07-15', 'St. Peter Parish', 'Baguio City, Philippines', '09123456789', 'juan@example.com', '2014-04-01', 'Baguio Cathedral', 'St. Peter Parish', 'St. Paul Parish', 'Seminary and theological studies', NULL, 'Active', 'Episcopal Diocese of Northern Philippines', '2027-03-31', 'Verified');

-- =========================================================
-- SEED DATA -- registrar / admin login (matches login.js demo credentials)
-- =========================================================

INSERT INTO admin_users (username, password, full_name, role) VALUES
  ('registrar', 'password', 'Registrar', 'Registrar');

