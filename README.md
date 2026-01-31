# AEC_Hackathon
# Heidelberg Materials Group - NextGen EcoNext
## Fleet Optimization & Route Pooling System

![Version](https://img.shields.io/badge/version-1.0.0-blue)
![Status](https://img.shields.io/badge/status-Production%20Ready-green)
![License](https://img.shields.io/badge/license-Proprietary-red)

---

## 📋 Project Overview

**NextGen EcoNext** is an advanced fleet management optimization system developed for Heidelberg Materials Group's concrete delivery operations in Hungary. The system uses intelligent route pooling and bin packing algorithms to significantly reduce transportation costs while maintaining service quality.

### Key Achievement
- **€282,189.73 savings** (30% cost reduction) across 8 months
- **2,895 optimized routes** requiring only 12m³ trucks
- **67.9% average truck utilization** across all routes
- **€60,273.72 average monthly savings**

---

## 🎯 Problem Statement

Heidelberg Materials Group faced inefficient concrete delivery routing with:
- Individual orders priced separately (€124/m³ for <3m³, €104.23/m³ for 3-7m³, €28.57/m³ for >7m³)
- No consolidation of nearby orders
- Underutilized truck capacity
- High per-order delivery costs
- Manual route planning

---

## ✨ Solution Features

### 1. **Intelligent Route Pooling**
- Combines multiple small orders into single truck deliveries
- Example: 9m³ + 3m³ = 12m³ (1 truck) instead of 2 separate deliveries
- Applies when orders are:
  - Same concrete type
  - Within 50km distance threshold
  - Can fit within truck capacity (7m³ or 12m³)

### 2. **Multi-Level Optimization Hierarchy**
```
Date/Month
  ↓
Concrete Type
  ↓
Distance Zone (<50km)
  ↓
Truck Size Selection
  ↓
Bin Packing Algorithm
```

### 3. **Advanced Bin Packing (First Fit Decreasing)**
- Sorts orders by size (largest first)
- Greedily fills 12m³ trucks first (lowest cost per m³)
- Falls back to 7m³ trucks if needed
- Minimizes total trucks required

### 4. **Daily & Monthly Analytics**
- Real-time cost comparison (before vs after pooling)
- Savings breakdown by date, concrete type, distance zone
- Truck fleet composition analysis
- Utilization metrics

### 5. **Dynamic Pricing Model**
| Scenario | Size | Price/m³ |
|----------|------|----------|
| **Before Pooling** | <3m³ | €124.00 |
| **Before Pooling** | 3-7m³ | €104.23 |
| **Before Pooling** | >7m³ | €28.57 |
| **After Pooling** | 7m³ truck | €104.23 |
| **After Pooling** | 12m³ truck | €28.57 |

---

